# Example task

Add a bounded retry helper to the target project. It must never retry validation errors, must use
exponential backoff capped at a configured maximum, and must expose deterministic tests using a fake
clock. Preserve all existing behavior and document the public interface.
