"""Short-lived anonymous JSON cache; authenticated responses never enter Redis."""
from __future__ import annotations

import hashlib
import time
from uuid import uuid4

from flask import current_app, g, has_app_context, request
from sqlalchemy import event
from sqlalchemy.orm import Session

PUBLIC_ENDPOINTS = {
    'competitions.list_competitions', 'competitions.competition_detail',
    'competitions.track_detail', 'competitions.list_problems', 'competitions.problem_detail',
    'content.list_content', 'content.content_detail', 'submissions.public_work_archive',
    'submissions.public_work', 'submissions.leaderboard',
    'profiles.profile',
}
PUBLIC_MODELS = {
    'User', 'Team', 'TeamMember', 'Registration', 'SubmissionAsset',
    'Competition', 'Track', 'Problem', 'Content', 'Submission', 'SubmissionVersion',
    'Review', 'CompetitionReviewer', 'Score', 'ScoreBatch', 'EvaluationRun',
}


def invalidate_public_cache():
    client = current_app.extensions.get('public_cache')
    if client:
        try:
            client.set(current_app.config['REDIS_CACHE_PREFIX'] + ':generation', uuid4().hex)
        except Exception:
            current_app.logger.warning('Public cache invalidation unavailable; entries expire by TTL')


@event.listens_for(Session, 'before_commit')
def remember_public_changes(session):
    # Writes may have been flushed already, so also collect them during flush.
    session.info['public_cache_changed'] = session.info.get('public_cache_changed', False) or any(
        type(item).__name__ in PUBLIC_MODELS
        and (item in session.new or item in session.deleted or session.is_modified(item, include_collections=False))
        for item in (*session.new, *session.dirty, *session.deleted))


@event.listens_for(Session, 'before_flush')
def remember_flushed_changes(session, _context, _instances):
    remember_public_changes(session)


@event.listens_for(Session, 'after_commit')
def invalidate_after_commit(session):
    changed = session.info.pop('public_cache_changed', False)
    if changed and has_app_context():
        invalidate_public_cache()


@event.listens_for(Session, 'after_rollback')
def discard_rolled_back_changes(session):
    session.info.pop('public_cache_changed', None)


@event.listens_for(Session, 'do_orm_execute')
def remember_bulk_public_changes(state):
    if state.is_update or state.is_delete:
        mapper = state.bind_mapper
        if mapper is not None and mapper.class_.__name__ in PUBLIC_MODELS:
            state.session.info['public_cache_changed'] = True


def init_public_cache(app):
    if not app.config.get('REDIS_URL'):
        return
    from redis import Redis
    app.extensions['public_cache'] = Redis.from_url(
        app.config['REDIS_URL'], socket_connect_timeout=.25, socket_timeout=.25,
        max_connections=16, health_check_interval=30,
    )

    @app.before_request
    def read_cached_public_json():
        if (request.method != 'GET' or request.endpoint not in PUBLIC_ENDPOINTS
                or request.cookies.get('session_token') or request.headers.get('Authorization')):
            return None
        client = app.extensions['public_cache']
        prefix = app.config['REDIS_CACHE_PREFIX']
        try:
            generation = client.get(prefix + ':generation') or b'0'
            digest = hashlib.sha256(request.full_path.encode()).hexdigest()
            key = prefix + ':' + generation.decode() + ':' + digest
            g.public_cache_key = key
            cached = client.get(key)
            if cached is not None:
                response = app.response_class(cached, content_type='application/json')
                response.headers['X-Cache'] = 'HIT'
                return response
        except Exception:
            # Cache failure must not stop database-backed pages or log credentials.
            g.public_cache_key = None
            now = time.monotonic()
            if now - app.extensions.get('public_cache_warning_at', 0) > 60:
                app.logger.warning('Public cache unavailable; serving from database')
                app.extensions['public_cache_warning_at'] = now
        return None

    @app.after_request
    def save_public_json(response):
        key = getattr(g, 'public_cache_key', None)
        if (key and response.status_code == 200 and response.is_json
                and 'Set-Cookie' not in response.headers and response.headers.get('X-Cache') != 'HIT'):
            try:
                body = response.get_data()
                if len(body) <= 1024 * 1024:
                    app.extensions['public_cache'].setex(key, app.config['REDIS_CACHE_TTL'], body)
                    response.headers['X-Cache'] = 'MISS'
            except Exception:
                pass
        return response
