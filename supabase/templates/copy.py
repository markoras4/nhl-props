"""Copy the branded auth emails to the clipboard, one field at a time, for pasting into the Supabase dashboard.

    py supabase/templates/copy.py              # all five emails
    py supabase/templates/copy.py magic_link   # just one

Open https://supabase.com/dashboard/project/evxnevqxykbfocqfqxid/auth/templates, pick the email's tab, then:
the script copies the subject -> paste it into "Subject heading", press Enter here,
it copies the body -> select all in "Message body" (Ctrl+A) and paste, press Enter here, then Save in the dashboard.
"""
import pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).parent
EMAILS = [  # file, dashboard tab, subject
    ("magic_link", "Magic link", "Your evenout sign-in link"),
    ("confirmation", "Confirm signup", "Confirm your evenout account"),
    ("recovery", "Reset password", "Reset your evenout password"),
    ("email_change", "Change email address", "Confirm your new email for evenout"),
    ("invite", "Invite user", "You're invited to evenout"),
]


def clip(text):
    subprocess.run("clip", input=text.encode("utf-16le"), check=True)   # Windows clipboard


def main():
    only = set(sys.argv[1:])
    for name, tab, subject in EMAILS:
        if only and name not in only:
            continue
        body = (HERE / f"{name}.html").read_text(encoding="utf-8")
        print(f"\n=== {tab} ===")
        clip(subject)
        input(f'Copied the subject "{subject}". Paste it into "Subject heading", then press Enter... ')
        clip(body)
        input('Copied the body. Click "Message body", Ctrl+A, Ctrl+V, click Save, then press Enter... ')
    print("\nDone. Send yourself a sign-in link on evenout.app to check it.")


if __name__ == "__main__":
    main()
