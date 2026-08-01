# Repository Guidelines

## Project Structure & Module Organization

This repository has a React frontend and FastAPI backend for streaming videos from Telegram.

- `backend/main.py`: HTTP routes, authentication, CORS, and byte-range parsing.
- `backend/telegram.py`: Telethon client, message lookup, thumbnails, and aligned streaming.
- `backend/config.py`: environment loading, ports, and Telegram download constants.
- `frontend/src/api/`: backend requests and media URL builders.
- `frontend/src/components/`: presentation components and the password gate.
- `frontend/src/hooks/`: reusable React data-fetching state.
- `frontend/src/App.jsx`: application composition and accordion behavior.

There is no test or committed asset directory. Keep `.env`, Telegram `session*` files, virtual environments, and `node_modules` out of Git.

## Build, Test, and Development Commands

Run backend commands from `backend/`:

- `uv sync`: create the virtual environment and install dependencies.
- `uv run python main.py`: start FastAPI with reload on the configured backend port.

Run frontend commands from `frontend/`:

- `npm install`: install JavaScript dependencies.
- `npm run dev`: start the Vite development server.
- `npm run build`: produce a production build.
- `npm run preview`: serve the production build locally.

No automated test or lint command is configured. Report only checks actually run.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` for Python functions and variables. Keep FastAPI routes focused on HTTP concerns; Telegram operations belong in `telegram.py`. Use two-space indentation for JavaScript/CSS, PascalCase for React components, camelCase for functions and state, and `useX` names for hooks. Preserve the centralized API base URL in `frontend/src/api/client.js`; components should use its helpers rather than construct URLs.

## Testing Guidelines

For backend changes, manually verify authentication, `/api/videos`, thumbnail loading, playback, and forward/backward seeking. For frontend changes, run `npm run build` and check desktop and mobile layouts. When adding tests, use `test_*.py` for Python and `*.test.jsx` for React, and document the runner in the relevant package configuration.

## Commit & Pull Request Guidelines

Write short, imperative commits such as `Fix suffix range parsing`. Keep unrelated backend and UI changes separate when practical. Pull requests should explain behavior changes, list commands or manual checks performed, identify configuration impacts, and include screenshots for visible UI changes. Link related issues when available.

## Security & Configuration

Store `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_CHANNEL`, `PW_HASH`, and `SESSION_SECRET` only in the root `.env`. Never commit credentials, bcrypt inputs, or Telethon session files. Keep HTTP range calculations in `main.py` synchronized with the 4096-byte alignment logic in `telegram.py`.
