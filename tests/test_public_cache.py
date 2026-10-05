import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
from platform_api.extensions import db
from platform_api.models import Competition, Content, User
from platform_api.public_cache import init_public_cache
from sqlalchemy import event


class PublicCacheTests(unittest.TestCase):
    tearDown = fixtures.PlatformApiTestCase.tearDown
    login = fixtures.PlatformApiTestCase.login

    def setUp(self):
        fixtures.PlatformApiTestCase.setUp(self)
        self.values = {}
        self.app.config.update(REDIS_URL='redis://localhost/15', REDIS_CACHE_TTL=30)
        init_public_cache(self.app)
        self.cache = Mock()
        self.cache.get.side_effect = self.values.get
        self.cache.set.side_effect = lambda key, value: self.values.update({key: value.encode() if isinstance(value, str) else value})
        self.cache.setex.side_effect = lambda key, ttl, value: self.values.update({key: value})
        self.app.extensions['public_cache'] = self.cache

    def test_anonymous_hits_skip_sql_queries_and_query_strings_are_separate(self):
        with self.app.app_context():
            queries = []
            def record(*args): queries.append(args[2])
            event.listen(db.engine, 'before_cursor_execute', record)
            try:
                first = self.member.get('/api/competitions?status=published')
                self.assertEqual(first.headers.get('X-Cache'), 'MISS')
                self.assertTrue(queries)
                queries.clear()
                second = self.member.get('/api/competitions?status=published')
                self.assertEqual(second.headers.get('X-Cache'), 'HIT')
                self.assertEqual(second.json, first.json)
                self.assertEqual(queries, [])
                draft = self.member.get('/api/competitions?status=draft')
                self.assertEqual(draft.headers.get('X-Cache'), 'MISS')
                self.assertEqual(draft.json, [])
            finally: event.remove(db.engine, 'before_cursor_execute', record)

    def test_authenticated_drafts_never_enter_anonymous_cache(self):
        with self.app.app_context():
            db.session.add(Competition(slug='private-competition', name='PRIVATE MARKER', summary='private', status='draft'))
            db.session.commit()
        anonymous = self.member.get('/api/competitions').json
        self.login(self.admin, 'admin@uestcai.top')
        privileged = self.admin.get('/api/competitions')
        self.assertNotIn('X-Cache', privileged.headers)
        self.assertIn('PRIVATE MARKER', privileged.get_data(as_text=True))
        again = self.member.get('/api/competitions')
        self.assertEqual(again.headers.get('X-Cache'), 'HIT')
        self.assertEqual(again.json, anonymous)
        self.assertNotIn('PRIVATE MARKER', again.get_data(as_text=True))
        denied = self.member.get('/api/manage/catalog')
        self.assertEqual(denied.status_code, 401)
        self.assertNotIn('X-Cache', denied.headers)

    def test_flushed_publication_changes_invalidate_and_rollback_keeps_cache(self):
        original = self.member.get('/api/content').json
        with self.app.app_context():
            item = Content(kind='announcement', slug='new-announcement', title='New', body_md='hello', status='published')
            db.session.add(item)
            db.session.flush()
            db.session.commit()
        changed = self.member.get('/api/content')
        self.assertEqual(changed.headers.get('X-Cache'), 'MISS')
        self.assertEqual(len(changed.json), len(original) + 1)
        with self.app.app_context():
            item = Content.query.filter_by(slug='new-announcement').one()
            item.status = 'draft'
            db.session.flush()
            db.session.rollback()
        self.assertEqual(self.member.get('/api/content').headers.get('X-Cache'), 'HIT')
        with self.app.app_context():
            Content.query.filter_by(slug='new-announcement').one().status = 'draft'
            db.session.commit()
        withdrawn = self.member.get('/api/content')
        self.assertEqual(withdrawn.headers.get('X-Cache'), 'MISS')
        self.assertEqual(withdrawn.json, original)
        with self.app.app_context():
            Content.query.filter_by(slug='new-announcement').update({'status': 'published'})
            db.session.commit()
        republished = self.member.get('/api/content')
        self.assertEqual(republished.headers.get('X-Cache'), 'MISS')
        self.assertEqual(len(republished.json), len(original) + 1)
        with self.app.app_context():
            Content.query.filter_by(slug='new-announcement').delete()
            db.session.commit()
        removed = self.member.get('/api/content')
        self.assertEqual(removed.headers.get('X-Cache'), 'MISS')
        self.assertEqual(removed.json, original)

    def test_redis_failure_falls_back_to_database_without_caching_errors(self):
        self.cache.get.side_effect = OSError('cache offline')
        response = self.member.get('/api/competitions')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('X-Cache', response.headers)
        self.assertTrue(response.json)

    def test_public_member_name_changes_invalidate_cache(self):
        self.member.get('/api/works')
        self.assertEqual(self.member.get('/api/works').headers.get('X-Cache'), 'HIT')
        with self.app.app_context():
            user = User.query.filter_by(email='admin@uestcai.top').one()
            user.name = 'Updated public member name'
            db.session.flush()
            db.session.commit()
        self.assertEqual(self.member.get('/api/works').headers.get('X-Cache'), 'MISS')


if __name__ == '__main__': unittest.main()
