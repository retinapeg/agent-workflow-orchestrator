class ArenaError(Exception):
    """Base error shown to CLI users without a traceback by default."""


class ConfigError(ArenaError):
    """Configuration is invalid."""


class RepositoryError(ArenaError):
    """Repository state violates a safety invariant."""


class ProviderError(ArenaError):
    """A provider could not be invoked safely."""


class EvaluationError(ArenaError):
    """Evaluation evidence is invalid or incomplete."""


class DeadlineExceeded(EvaluationError):
    """The coordinator's absolute whole-run deadline was exhausted."""


class IntegrityError(ArenaError):
    """An audit artifact failed integrity verification."""
