from .extensions import db
from .models import TimestampMixin, iso, new_id


class CheckpointEntry(TimestampMixin, db.Model):
    __tablename__ = "checkpoint_entries"
    __table_args__ = (db.UniqueConstraint("problem_id", "team_id", "checkpoint_id", name="uq_checkpoint_team_problem"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    checkpoint_id = db.Column(db.String(32), nullable=False)
    revision = db.Column(db.Integer, nullable=False, default=1)
    content_md = db.Column(db.Text, nullable=False)
    repository = db.Column(db.String(1000), nullable=False, default="")
    evaluation_run_id = db.Column(db.String(36), db.ForeignKey("evaluation_runs.id", ondelete="SET NULL"))
    package_storage_name = db.Column(db.String(255))
    package_name = db.Column(db.String(255))
    package_sha256 = db.Column(db.String(64))
    feedback_md = db.Column(db.Text, nullable=False, default="")
    problem = db.relationship("Problem")
    team = db.relationship("Team")

    def to_dict(self):
        return {"id": self.id, "problem_id": self.problem_id, "team_id": self.team_id,
                "problem_title": self.problem.title, "team_name": self.team.name,
                "checkpoint_id": self.checkpoint_id, "revision": self.revision,
                "content_md": self.content_md, "repository": self.repository,
                "evaluation_run_id": self.evaluation_run_id,
                "package_name": self.package_name, "package_sha256": self.package_sha256,
                "package_url": f"/api/checkpoints/{self.id}/package" if self.package_storage_name else None,
                "feedback_md": self.feedback_md, "created_at": iso(self.created_at), "updated_at": iso(self.updated_at)}


class EvaluationArtifact(db.Model):
    __tablename__ = "evaluation_artifacts"
    __table_args__ = (db.UniqueConstraint("run_id", "name", name="uq_evaluation_artifact_name"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    run_id = db.Column(db.String(36), db.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    storage_name = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    run = db.relationship("EvaluationRun", back_populates="artifacts")

    def to_dict(self):
        return {"id": self.id, "name": self.name, "size": self.size, "url": f"/api/evaluation-artifacts/{self.id}"}
