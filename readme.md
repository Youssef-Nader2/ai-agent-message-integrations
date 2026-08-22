# AI Agent Message Integrations

Shared backend foundation for connecting message providers to an AI-agent ingestion workflow.

## Architecture

- FastAPI application entry point in `app/main.py`
- SQLAlchemy database foundation configured through `DATABASE_URL`
- PostgreSQL is the intended application database, configured through `DATABASE_URL`
- Provider integrations live under `app/integrations/`

## Local setup

1. Create and activate a Python virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and adjust `DATABASE_URL` if needed.
4. Run the API with `uvicorn app.main:app --reload`.

The health endpoint is available at `/health`.

## Integrations

Discord authorization and read-only message loading are available. Slack and Telegram are intended sibling integrations under `app/integrations/`.

Never commit credentials, OAuth tokens, bot tokens, or `.env` files.

## Discord environment variables

Configure these values in `.env`:

- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_REDIRECT_URI`
- `DISCORD_BOT_TOKEN`

## Discord Developer Portal setup

Create a Discord application with a bot user and register a callback URL matching
`DISCORD_REDIRECT_URI`. The bot installation requests only `VIEW_CHANNEL` and
`READ_MESSAGE_HISTORY`; it is read-only and does not request Administrator or Send
Messages. Enable the `MESSAGE_CONTENT` privileged intent when message content,
embeds, or attachments must be available.

The Discord console discovers connected servers from persisted OAuth connections,
then uses the application Bot token to discover standard text (`0`) and announcement
(`5`) channels. A single connected server is selected automatically; multiple servers
are displayed by name for selection. The console does not accept manually entered
Guild or Channel IDs, reads up to the requested limit per readable channel, and skips
individual channels the Bot cannot read. One Bot installation is shared per Discord
application/server; users do not each need a Bot, and multiple users may connect the
same server. Message history remains Bot-token-only and read-only. Production hosts
must still protect the console with their own application authentication and
authorization.

## Persistence and security

PostgreSQL is the application database. SQLite is used only by isolated automated tests.
Tables are currently bootstrapped during FastAPI startup with SQLAlchemy `create_all`;
production deployments should adopt a migration system.
Existing databases created before the Discord multi-user connection change require a
schema migration to replace `unique(guild_id)` with
`unique(discord_user_id, guild_id)`; `create_all` does not alter existing constraints.
OAuth tokens are persisted for this assignment. Production deployments should add
encryption at rest and managed key handling.
