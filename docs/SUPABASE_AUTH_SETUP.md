# Supabase invitation and redirect setup

The frontend accepts Supabase invitation and password-recovery callbacks on the application root URL. Invited users are asked to create a password before entering the application.

## Supabase dashboard

In **Authentication → URL Configuration** set:

- **Site URL** to the deployed frontend origin, for example `https://your-frontend.example.com`.
- **Redirect URLs** to every exact frontend origin that may receive an invitation:
  - `https://your-frontend.example.com/**`
  - `http://localhost:3000/**` for local testing only.

Do not use the backend API URL as the Site URL or invitation redirect. Supabase must return the browser to the Next.js frontend.

When inviting a user from the Supabase dashboard, leave the redirect target on the configured Site URL. If invitations are sent through an API, set `redirectTo` to the same frontend origin.

## Frontend environment

Configure these public values in the frontend deployment:

```text
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_KEY
NEXT_PUBLIC_API_BASE_URL=https://YOUR_BACKEND.example.com
```

Redeploy the frontend after changing environment variables. Existing invitation emails may contain the old redirect URL, so send a new invitation after correcting the configuration.

## Verification

1. Invite a new email address.
2. Open the email link in a private browser window.
3. Confirm the **Set your password** page appears.
4. Save a password of at least eight characters and confirm the application opens.
5. Sign out, then sign in with the new password.
6. Reopen the old invitation link and confirm the expired/used-link guidance appears instead of a blank or broken page.
