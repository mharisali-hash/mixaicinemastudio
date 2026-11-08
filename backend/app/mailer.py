"""Account emails (contract v38). EMAIL_BACKEND=console logs them (dev), smtp sends with the stdlib in a thread."""
import html
import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid

from app.config import get_settings

log = logging.getLogger("mixai.mail")

STUDIO = "Jafat AI Studio"
# Antique Atelier paper (frontend/src/index.css); email clients ignore most CSS, so it's all inline
PAPER, CARD, BORDER, INK, MUTED, ACCENT, ON_ACCENT = ("#e4d2ab", "#f4ead5", "#b8862f", "#2a1c0f", "#5b4630",
                                                      "#7a4e1a", "#f4ead5")
SERIF = "'Cormorant Garamond', 'Iowan Old Style', Georgia, 'Times New Roman', serif"
SANS = "'Inter', 'Segoe UI', Helvetica, Arial, sans-serif"


@dataclass
class Mail:
    to: str
    subject: str
    text: str
    html: str


def _html(title: str, paragraphs: list[str], button: tuple[str, str] | None, footer: str) -> str:
    body = "".join(f'<p style="margin:0 0 14px;font:15px/22px {SANS};color:{INK};">{html.escape(p)}</p>'
                   for p in paragraphs)
    if button:
        label, url = button
        body += (f'<p style="margin:22px 0;"><a href="{html.escape(url)}" style="display:inline-block;'
                 f'background:{ACCENT};color:{ON_ACCENT};font:600 15px/20px {SANS};text-decoration:none;'
                 f'padding:11px 20px;border-radius:6px;">{html.escape(label)}</a></p>'
                 f'<p style="margin:0 0 14px;font:13px/20px {SANS};color:{MUTED};">If the button doesn\'t work, '
                 f'paste this into your browser:<br><span style="word-break:break-all;color:{ACCENT};">'
                 f'{html.escape(url)}</span></p>')
    return (f'<!doctype html><html lang="en-GB"><body style="margin:0;padding:0;background:{PAPER};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER};">'
            f'<tr><td align="center" style="padding:32px 16px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;'
            f'background:{CARD};border:3px double {BORDER};border-radius:8px;">'
            f'<tr><td style="padding:28px 28px 8px;">'
            f'<p style="margin:0 0 4px;font:12px/16px {SANS};letter-spacing:.08em;text-transform:uppercase;'
            f'color:{MUTED};">{STUDIO}</p>'
            f'<h1 style="margin:0 0 18px;font:700 28px/34px {SERIF};color:{INK};">{html.escape(title)}</h1>'
            f'{body}</td></tr>'
            f'<tr><td style="padding:0 28px 24px;font:12px/18px {SANS};color:{MUTED};">{html.escape(footer)}</td></tr>'
            f'</table></td></tr></table></body></html>')


def _text(title: str, paragraphs: list[str], button: tuple[str, str] | None, footer: str) -> str:
    parts = [title, "", *[p + "\n" for p in paragraphs]]
    if button:
        parts += [f"{button[0]}: {button[1]}", ""]
    parts += ["--", footer, STUDIO]
    return "\n".join(parts)


def compose(to: str, subject: str, title: str, paragraphs: list[str], button: tuple[str, str] | None = None,
            footer: str = "You're getting this because someone used this address at the studio.") -> Mail:
    return Mail(to=to, subject=subject, text=_text(title, paragraphs, button, footer),
                html=_html(title, paragraphs, button, footer))


def link(path: str) -> str:
    return get_settings().public_base + path


def verify_mail(to: str, name: str, url: str) -> Mail:
    return compose(to, "Confirm your email", f"Welcome{', ' + name if name else ''}",
                   ["Confirm this is your email address and your studio is ready to start creating.",
                    "The link works for 48 hours."],
                   ("Confirm my email", url),
                   "If you didn't sign up, ignore this email and nothing will happen.")


def reset_mail(to: str, url: str) -> Mail:
    return compose(to, "Reset your password", "Choose a new password",
                   ["Someone asked to reset the password for this account. If it was you, choose a new one below.",
                    "The link works for one hour and only once. Every browser signed in to the account is signed "
                    "out when you change it."],
                   ("Choose a new password", url),
                   "If you didn't ask for this, ignore this email; your password stays as it is.")


def exists_mail(to: str, sign_in: str, forgot: str) -> Mail:
    # sign-up with a known address says the same thing on screen; only the inbox owner learns the difference
    return compose(to, "You already have an account", "You already have an account",
                   ["Someone tried to sign up with this email address, but there's already an account for it.",
                    f"Sign in at {sign_in}, or if you've forgotten the password, reset it at {forgot}."],
                   ("Sign in", sign_in),
                   "If this wasn't you, ignore this email; nothing has changed.")


def invite_mail(to: str, name: str, url: str) -> Mail:
    return compose(to, f"Your {STUDIO} account", f"Welcome{', ' + name if name else ''}",
                   ["An account has been made for you at the studio. Choose a password to sign in.",
                    "The link works for seven days."],
                   ("Choose my password", url),
                   "If you weren't expecting this, ignore this email.")


# ---------------------------------------------------------------- delivery

def _message(m: Mail) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["Subject"] = m.subject
    msg["From"] = s.smtp_from or s.smtp_user or f"studio@{s.smtp_host or 'localhost'}"
    msg["To"] = m.to
    msg["Message-ID"] = make_msgid(domain=(s.smtp_from.rpartition("@")[2].strip("> ") or None))
    msg.set_content(m.text)
    msg.add_alternative(m.html, subtype="html")
    return msg


def _smtp_send(m: Mail) -> None:
    s = get_settings()
    mode = s.smtp_tls.strip().lower()
    try:
        if mode == "ssl":
            conn = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30, context=ssl.create_default_context())
        else:
            conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
        with conn:
            if mode == "starttls":
                conn.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                conn.login(s.smtp_user, s.smtp_password)
            conn.send_message(_message(m))
        log.info("sent '%s' to %s", m.subject, m.to)
    except Exception:
        # nobody is waiting on this thread; the log is where a bad SMTP setting shows up
        log.exception("couldn't send '%s' to %s", m.subject, m.to)


def can_send() -> bool:
    # the console backend only logs; nothing reaches a real inbox
    return get_settings().email_backend.strip().lower() == "smtp"


def deliver(m: Mail) -> None:
    if get_settings().email_backend.strip().lower() == "smtp":
        threading.Thread(target=_smtp_send, args=(m,), name="mailer", daemon=True).start()
        return
    log.warning("email (console backend) to %s: %s\n%s", m.to, m.subject, m.text)
