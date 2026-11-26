# evenout auth emails

Branded templates for the Supabase Auth emails (sign-in link, sign-up confirmation, password reset, email change, invite).
Supabase doesn't read this folder: paste each file into the dashboard, Authentication > Emails > Templates
(Supabase's field name: subject and "Message body").

| Email | File | Subject |
|---|---|---|
| Magic Link | `magic_link.html` | Your evenout sign-in link |
| Confirmation | `confirmation.html` | Confirm your evenout account |
| Recovery | `recovery.html` | Reset your evenout password |
| Email Change | `email_change.html` | Confirm your new email for evenout |
| Invite | `invite.html` | You're invited to evenout |

The logo is https://evenout.app/icons/email-logo.png (a PNG, since mail apps block SVG).

## Sender
The sender name and address are set under Authentication > Emails > SMTP Settings (custom SMTP), using the
support@evenout.app mailbox at Namecheap Private Email: host mail.privateemail.com, port 465, user support@evenout.app,
sender name "evenout". The mailbox password is typed there by the owner only; it is never stored in this repo.
