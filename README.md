# Lynkup Backend

Backend service for **Lynkup** (KampuLynk) — This API handles user registration and authentication, profiles, search and discovery, file uploads, and admin management.

Built with **FastAPI**, **SQLModel/SQLAlchemy**, **PostgreSQL**, and **Firebase Authentication**.

---

## Features

| Module             | Description                                                               |
| :----------------- | :------------------------------------------------------------------------ |
| **Accounts**       | Signup, login, OTP verification, forgot-password, session refresh, logout |
| **Profiles**       | User profile CRUD, academic interests, universities                       |
| **Search**         | Search users by name, keyword, hashtag; filter by university or interest  |
| **Uploads**        | Profile images, banners, and post media                                   |
| **Administration** | Admin signup, onboarding, and user management                             |

---

## Tech Stack

- **Framework:** FastAPI + Uvicorn
- **ORM / DB:** SQLModel, SQLAlchemy, Alembic (PostgreSQL via `asyncpg`)
- **Auth:** Firebase Admin SDK + JWT
- **Email:** SendGrid
- **Testing:** pytest, pytest-cov, httpx

---

## Prerequisites

- Python 3.11+
- PostgreSQL
- Firebase project (service account credentials)
- SendGrid API key (for transactional email)

---

## Getting Started

### 1. Clone and install dependencies

```bash
git clone <repository-url>
cd Lynkup-Backend
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Key variables:

| Variable                        | Description                                    |
| :------------------------------ | :--------------------------------------------- |
| `DATABASE_URL`                  | PostgreSQL connection string                   |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | Path to Firebase service account JSON          |
| `FIREBASE_PROJECT_ID`           | Firebase project ID                            |
| `FIREBASE_WEB_API_KEY`          | Firebase Web API key                           |
| `SENDGRID_API_KEY`              | SendGrid API key                               |
| `SENDGRID_FROM_EMAIL`           | Sender email address                           |
| `JWT_SECRET`                    | Secret for signing JWT access tokens           |
| `AUTO_INIT_DB`                  | Set to `true` to run database setup on startup |

### 3. Run database migrations

```bash
alembic upgrade head
```

### 4. Start the development server

#### Direct Fastapi startup

```bash
PORT=8000 RELOAD=true python -m entrypoints.api
```

The API will be available at `http://localhost:8000`.

#### Using Dockerfile

Build docker server

```bash
  docker build -t kampulynk-backend:local .
```

Start server

```bash
1. docker run --rm -p 8080:8080 --env-file .env kampulynk-backend:local


2. docker run --rm -p 8080:8080 \
  --env-file .env \
  -e DATABASE_URL="postgresql://kampulynk:kampulynk_postgres@host.docker.internal:5432/kampulynk?sslmode=disable" \
  kampulynk-backend:local
```

---

## API Documentation

With the server running:

- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **OpenAPI JSON:** [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

All routes are prefixed with `/api/v1`. Authenticated endpoints require a Firebase ID token in the `Authorization: Bearer <token>` header.

---

## Running Tests

Run the unit test suite with coverage over the `apps` package:

```bash
python -m pytest --cov=apps tests\unit
```

Coverage is also configured in `pytest.ini` (target: 80% across `apps` and `core`). To generate an HTML coverage report:

```bash
python -m pytest --cov=apps --cov=core --cov-report=html tests\unit
```

Open `htmlcov/index.html` in a browser to view the report.

---

## Project Structure

```
Lynkup-Backend/
├── apps/
│   ├── accounts/        # Auth, signup, OTP, logout
│   ├── administration/  # Admin routes and services
│   ├── profiles/        # User profile management
│   ├── search/          # Search and discovery
│   └── uploads/         # File upload endpoints
├── core/
│   ├── auth/            # Firebase and JWT dependencies
│   ├── database/        # DB config, session, init
│   ├── security/        # Auth helpers
│   └── email_service.py # SendGrid + email cron worker
├── entrypoints/
│   └── api.py           # FastAPI application entry point
├── alembic/             # Database migrations
├── templates/           # Email HTML templates
├── tests/unit/          # Unit tests
└── requirements.txt
```

---

## Database Migrations

Migrations are managed with Alembic:

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "describe your change"
```

---

## Release Notes

For Milestone 1 changes (Firebase forgot-password flow, logout/device tracking, schema updates), see [docs/milestone-1-release-note.md](docs/milestone-1-release-note.md).
