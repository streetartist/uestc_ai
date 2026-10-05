"""Competition AI gateway records. Secrets never appear in public serializers."""
from .extensions import db
from .models import TimestampMixin, iso, new_id


class AIChannel(TimestampMixin, db.Model):
    __tablename__ = "ai_channels"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    name = db.Column(db.String(80), nullable=False, unique=True)
    base_url = db.Column(db.String(500), nullable=False)
    protocol = db.Column(db.String(24), nullable=False, default="openai")
    secrets = db.Column(db.Text, nullable=False)
    models = db.Column(db.JSON, nullable=False, default=dict)
    disabled_models = db.Column(db.JSON, nullable=False, default=list)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    weight = db.Column(db.Integer, nullable=False, default=1)
    priority = db.Column(db.Integer, nullable=False, default=0)
    input_price = db.Column(db.Float, nullable=False, default=0)
    output_price = db.Column(db.Float, nullable=False, default=0)
    last_test = db.Column(db.JSON)

    def to_dict(self):
        from .ai_gateway import decrypt_keys
        return {"id": self.id, "name": self.name, "base_url": self.base_url,
                "protocol": self.protocol, "models": self.models, "disabled_models": self.disabled_models, "enabled": self.enabled,
                "weight": self.weight, "priority": self.priority,
                "input_price": self.input_price, "output_price": self.output_price,
                "key_count": len(decrypt_keys(self.secrets)), "last_test": self.last_test}


class AIProblemQuota(TimestampMixin, db.Model):
    __tablename__ = "ai_problem_quotas"
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True)
    config = db.Column(db.JSON, nullable=False)
    problem = db.relationship("Problem", back_populates="ai_quota_policy")


class AIGrant(TimestampMixin, db.Model):
    __tablename__ = "ai_grants"
    __table_args__ = (
        db.UniqueConstraint("team_id", "problem_id", name="uq_ai_grants_team_problem"),
        db.Index("uq_ai_grants_team_default", "team_id", unique=True,
                 sqlite_where=db.text("problem_id IS NULL"), postgresql_where=db.text("problem_id IS NULL")),
    )
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False, index=True)
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id", name="fk_ai_grants_problem_id"), index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    allowed_models = db.Column(db.JSON, nullable=False)
    allowed_channels = db.Column(db.JSON, nullable=False, default=list)
    max_calls = db.Column(db.Integer, nullable=False)
    max_tokens = db.Column(db.BigInteger, nullable=False)
    max_cost_micros = db.Column(db.BigInteger)
    max_output_tokens = db.Column(db.Integer, nullable=False, default=4096)
    requests_per_minute = db.Column(db.Integer, nullable=False, default=60)
    max_concurrent = db.Column(db.Integer, nullable=False, default=2)
    calls_used = db.Column(db.Integer, nullable=False, default=0)
    tokens_used = db.Column(db.BigInteger, nullable=False, default=0)
    cost_used_micros = db.Column(db.BigInteger, nullable=False, default=0)
    team = db.relationship("Team")
    problem = db.relationship("Problem")

    def to_dict(self):
        return {"id": self.id, "team_id": self.team_id, "team_name": self.team.name,
                "problem_id": self.problem_id, "problem_title": self.problem.title if self.problem else None,
                "competition_id": self.team.competition_id,
                "competition_name": self.team.competition.name,
                "enabled": self.enabled,
                "allowed_models": self.allowed_models, "allowed_channels": self.allowed_channels,
                **{name: getattr(self, name) for name in (
                    "max_calls", "max_tokens", "max_cost_micros",
                    "max_output_tokens", "requests_per_minute", "max_concurrent", "calls_used",
                    "tokens_used", "cost_used_micros")}}


class AIKey(TimestampMixin, db.Model):
    __tablename__ = "ai_keys"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    grant_id = db.Column(db.String(36), db.ForeignKey("ai_grants.id"), nullable=False, index=True)
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    key_hash = db.Column(db.String(64), nullable=False, unique=True)
    prefix = db.Column(db.String(20), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    grant = db.relationship("AIGrant")

    def to_dict(self):
        return {"id": self.id, "grant_id": self.grant_id, "name": self.name, "prefix": self.prefix,
                "enabled": self.enabled, "created_at": iso(self.created_at)}


class AIUsage(TimestampMixin, db.Model):
    __tablename__ = "ai_usage"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    grant_id = db.Column(db.String(36), db.ForeignKey("ai_grants.id"), nullable=False, index=True)
    key_id = db.Column(db.String(36), db.ForeignKey("ai_keys.id"), index=True)
    evaluation_run_id = db.Column(db.String(36), db.ForeignKey("evaluation_runs.id"), index=True)
    channel_id = db.Column(db.String(36), db.ForeignKey("ai_channels.id"))
    model = db.Column(db.String(160), nullable=False, index=True)
    endpoint = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="pending", index=True)
    deadline = db.Column(db.DateTime(timezone=True), nullable=False)
    input_tokens = db.Column(db.BigInteger)
    output_tokens = db.Column(db.BigInteger)
    cached_tokens = db.Column(db.BigInteger)
    charged_tokens = db.Column(db.BigInteger, nullable=False)
    charged_cost_micros = db.Column(db.BigInteger, nullable=False)
    latency_ms = db.Column(db.Integer)
    first_token_ms = db.Column(db.Integer)
    attempts = db.Column(db.JSON, nullable=False, default=list)
    error_code = db.Column(db.String(80))
    grant = db.relationship("AIGrant")

    def to_dict(self):
        return {"id": self.id, "grant_id": self.grant_id, "team_name": self.grant.team.name,
                "problem_id": self.grant.problem_id, "problem_title": self.grant.problem.title if self.grant.problem else None,
                "key_id": self.key_id, "evaluation_run_id": self.evaluation_run_id,
                "channel_id": self.channel_id, "model": self.model, "endpoint": self.endpoint,
                "status": self.status, "created_at": iso(self.created_at), "deadline": iso(self.deadline),
                **{name: getattr(self, name) for name in ("input_tokens", "output_tokens", "cached_tokens",
                    "charged_tokens", "charged_cost_micros", "latency_ms",
                    "first_token_ms", "attempts", "error_code")}}
