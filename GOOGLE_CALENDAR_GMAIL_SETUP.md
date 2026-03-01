# Google Calendar and Gmail Setup Guide

This guide explains how to configure Google Calendar and Gmail integration for the Enhanced Resume Agent. The system uses OAuth 2.0 for read access to Calendar and Gmail, and SMTP for sending emails.

## Prerequisites

You need a Google account and access to the Google Cloud Console at https://console.cloud.google.com.

## Step 1: Create a Google Cloud Project

Navigate to the Google Cloud Console and create a new project (or select an existing one). Give it a descriptive name such as "Resume Agent" and note the project ID.

## Step 2: Enable Required APIs

From the project dashboard, go to **APIs & Services > Library** and enable the following APIs:

| API | Purpose |
|-----|---------|
| Google Calendar API | Read/write calendar events for interview scheduling |
| Gmail API | Read inbox messages for email sync and classification |

## Step 3: Configure OAuth Consent Screen

Go to **APIs & Services > OAuth consent screen** and configure the following:

Set the user type to **External** (or Internal if using Google Workspace). Fill in the application name, support email, and developer contact. Under **Scopes**, add `https://www.googleapis.com/auth/calendar` and `https://www.googleapis.com/auth/gmail.readonly`. Add your email address as a test user.

## Step 4: Create OAuth 2.0 Credentials

Go to **APIs & Services > Credentials** and click **Create Credentials > OAuth client ID**. Select **Web application** as the application type. Set the name to "Resume Agent". Under **Authorized redirect URIs**, add `http://localhost:8000/callback`. Download the JSON file and save it as `google_api_server/credentials.json`.

## Step 5: Authorize the Application

Start the FastAPI server and navigate to the authorization endpoint:

```bash
python main.py server
# Open http://localhost:8000/auth in your browser
```

Follow the Google OAuth flow to grant access. After authorization, a `token.json` file will be saved in `google_api_server/`. This token persists across restarts and will auto-refresh when expired.

## Step 6: Configure Gmail SMTP (for Sending Emails)

The Email Composer agent sends emails via Gmail SMTP, which requires an app password. Go to your Google Account settings at https://myaccount.google.com/security. Enable 2-Step Verification if not already enabled. Under **App passwords**, generate a new app password for "Mail". Copy the 16-character password and set it in your `.env` file:

```
GMAIL_USER=your-email@gmail.com
GMAIL_APP_PASSWORD=your-16-char-app-password
```

## Verification

After completing setup, verify the integration by running:

```bash
# Test Calendar access
curl http://localhost:8000/calendar/events

# Test Gmail access
curl http://localhost:8000/gmail/messages
```

Both endpoints should return JSON data. If you receive a 401 error, re-run the OAuth flow at `/auth`.

## Troubleshooting

**"credentials.json not found"** — Download the OAuth client JSON from Google Cloud Console and place it in `google_api_server/credentials.json`.

**"Not authorized"** — Open `http://localhost:8000/auth` in your browser to complete the OAuth flow.

**"Token expired"** — The server auto-refreshes tokens. If refresh fails, delete `google_api_server/token.json` and re-authorize.

**SMTP authentication failed** — Ensure 2-Step Verification is enabled and you are using an app password (not your regular Google password).

**"Access blocked: This app's request is invalid"** — Verify that the redirect URI in Google Cloud Console matches exactly: `http://localhost:8000/callback`.
