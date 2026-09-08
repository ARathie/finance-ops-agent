"""SQLite storage: the agent's own database (data/agent.db).

Money is whole cents and hours whole hundredths (INTEGER); dates are ISO text
via the Date type; timestamps are ISO-8601 UTC text so nothing ever passes
through a float. Schema changes are Alembic migrations under alembic/.
"""
