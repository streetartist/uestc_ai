"""GPU allocations are independent of model tokens and private to a team."""
from .extensions import db
from .models import TimestampMixin, iso, new_id


class ComputeProvider(TimestampMixin, db.Model):
    __tablename__ = "compute_providers"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    name = db.Column(db.String(80), nullable=False, unique=True)
    base_url = db.Column(db.String(300), nullable=False, default="https://api.autodl.com")
    secret = db.Column(db.Text, nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    image_uuid = db.Column(db.String(160), nullable=False)
    gpu_spec_uuid = db.Column(db.String(80), nullable=False)
    gpu_label = db.Column(db.String(120), nullable=False)
    gpu_count = db.Column(db.Integer, nullable=False, default=1)
    hourly_price_millis = db.Column(db.Integer, nullable=False)
    cuda_v_from = db.Column(db.Integer, nullable=False, default=113)
    data_centers = db.Column(db.JSON, nullable=False, default=list)

    def to_dict(self):
        return {"id": self.id, "kind": "autodl-pro", "name": self.name, "enabled": self.enabled,
                "has_token": bool(self.secret), **{name: getattr(self, name) for name in (
                    "base_url", "image_uuid", "gpu_spec_uuid", "gpu_label", "gpu_count",
                    "hourly_price_millis", "cuda_v_from", "data_centers")}}


class ComputePolicy(TimestampMixin, db.Model):
    __tablename__ = "compute_policies"
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True)
    config = db.Column(db.JSON, nullable=False)
    problem = db.relationship("Problem", back_populates="compute_policy")


class ComputeGrant(TimestampMixin, db.Model):
    __tablename__ = "compute_grants"
    __table_args__ = (db.UniqueConstraint("team_id", "problem_id", name="uq_compute_grant_team_problem"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False, index=True)
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id"), nullable=False, index=True)
    provider_id = db.Column(db.String(36), db.ForeignKey("compute_providers.id"), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    max_gpu_seconds = db.Column(db.BigInteger, nullable=False)
    max_cost_millis = db.Column(db.BigInteger)
    gpu_seconds_used = db.Column(db.BigInteger, nullable=False, default=0)
    cost_used_millis = db.Column(db.BigInteger, nullable=False, default=0)
    team = db.relationship("Team")
    problem = db.relationship("Problem")
    provider = db.relationship("ComputeProvider")

    def to_dict(self):
        return {"id": self.id, "team_id": self.team_id, "team_name": self.team.name,
                "problem_id": self.problem_id, "problem_title": self.problem.title,
                "competition_id": self.team.competition_id, "competition_name": self.team.competition.name,
                "provider_id": self.provider_id, "provider_name": self.provider.name,
                "gpu_label": self.provider.gpu_label, "gpu_count": self.provider.gpu_count,
                "hourly_price_millis": self.provider.hourly_price_millis,
                **{name: getattr(self, name) for name in ("enabled", "max_gpu_seconds", "max_cost_millis",
                                                        "gpu_seconds_used", "cost_used_millis")}}


class ComputeInstance(TimestampMixin, db.Model):
    __tablename__ = "compute_instances"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    grant_id = db.Column(db.String(36), db.ForeignKey("compute_grants.id"), nullable=False, unique=True)
    provider_id = db.Column(db.String(36), db.ForeignKey("compute_providers.id"), nullable=False)
    remote_id = db.Column(db.String(160), unique=True)
    name = db.Column(db.String(120), nullable=False)
    provider_status = db.Column(db.String(32), nullable=False, default="uncreated")
    grant = db.relationship("ComputeGrant")
    provider = db.relationship("ComputeProvider")

    def to_dict(self):
        return {"id": self.id, "grant_id": self.grant_id, "name": self.name,
                "remote_id": self.remote_id, "provider_status": self.provider_status,
                "provider_name": self.provider.name, "created_at": iso(self.created_at)}


class ComputeSession(TimestampMixin, db.Model):
    __tablename__ = "compute_sessions"
    __table_args__ = (db.UniqueConstraint("grant_id", "request_id", name="uq_compute_session_request"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    grant_id = db.Column(db.String(36), db.ForeignKey("compute_grants.id"), nullable=False, index=True)
    instance_id = db.Column(db.String(36), db.ForeignKey("compute_instances.id"), nullable=False)
    request_id = db.Column(db.String(80), nullable=False)
    requested_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    state = db.Column(db.String(32), nullable=False, default="queued", index=True)
    duration_seconds = db.Column(db.Integer, nullable=False)
    gpu_count = db.Column(db.Integer, nullable=False)
    rate_millis = db.Column(db.Integer, nullable=False)
    reserved_gpu_seconds = db.Column(db.BigInteger, nullable=False)
    reserved_cost_millis = db.Column(db.BigInteger, nullable=False)
    charged_gpu_seconds = db.Column(db.BigInteger)
    charged_cost_millis = db.Column(db.BigInteger)
    dispatched_at = db.Column(db.DateTime(timezone=True))
    deadline = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    stop_requested = db.Column(db.Boolean, nullable=False, default=False)
    error_code = db.Column(db.String(80))
    lease_owner = db.Column(db.String(80))
    lease_expires_at = db.Column(db.DateTime(timezone=True))
    next_check_at = db.Column(db.DateTime(timezone=True), nullable=False)
    instance = db.relationship("ComputeInstance")
    grant = db.relationship("ComputeGrant")

    def to_dict(self):
        return {"id": self.id, "grant_id": self.grant_id, "instance_id": self.instance_id,
                "team_name": self.grant.team.name, "problem_title": self.grant.problem.title,
                **{name: getattr(self, name) for name in ("state", "duration_seconds", "gpu_count",
                    "rate_millis", "reserved_gpu_seconds", "reserved_cost_millis", "charged_gpu_seconds",
                    "charged_cost_millis", "stop_requested", "error_code")},
                **{name: iso(getattr(self, name)) for name in ("created_at", "dispatched_at", "deadline", "finished_at")}}


class ComputeWorkerHeartbeat(db.Model):
    __tablename__ = "compute_worker_heartbeats"
    id = db.Column(db.String(80), primary_key=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
