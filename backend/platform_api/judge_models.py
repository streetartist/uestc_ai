"""Organizer judge instances are separate from participant compute grants."""
from .extensions import db
from .models import TimestampMixin, new_id, iso


class JudgePool(TimestampMixin, db.Model):
    __tablename__ = "evaluation_judge_pools"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id"), nullable=False, unique=True)
    provider_id = db.Column(db.String(36), db.ForeignKey("compute_providers.id"), nullable=False)
    remote_id = db.Column(db.String(160), nullable=False, unique=True)
    image = db.Column(db.String(160), nullable=False)
    worker_id = db.Column(db.String(80), nullable=False, unique=True)
    dataset_manifests = db.Column(db.JSON, nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    idle_seconds = db.Column(db.Integer, nullable=False, default=120)
    boot_seconds = db.Column(db.Integer, nullable=False, default=600)
    state = db.Column(db.String(24), nullable=False, default="off")
    provider_status = db.Column(db.String(32), nullable=False, default="unknown")
    error = db.Column(db.String(300))
    checked_at = db.Column(db.DateTime(timezone=True))
    dispatched_at = db.Column(db.DateTime(timezone=True))
    idle_since = db.Column(db.DateTime(timezone=True))
    retry_at = db.Column(db.DateTime(timezone=True))
    lease_owner = db.Column(db.String(80))
    lease_expires_at = db.Column(db.DateTime(timezone=True))
    provider = db.relationship("ComputeProvider")

    def to_dict(self):
        return {"id": self.id, "enabled": self.enabled, "remote_id": self.remote_id,
                "provider_id": self.provider_id, "image": self.image, "worker_id": self.worker_id,
                "state": self.state, "provider_status": self.provider_status, "error": self.error,
                "idle_seconds": self.idle_seconds, "boot_seconds": self.boot_seconds,
                "checked_at": iso(self.checked_at), "dispatched_at": iso(self.dispatched_at)}
