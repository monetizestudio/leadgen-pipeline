"""Fill the message templates for one lead."""
from . import config


def kind(niche):
    return "realestate" if niche == "Real Estate" else "design"


def fields(company, first_name, niche, city, observation):
    return {
        "company": company,
        "first_name": first_name or "there",
        "niche": config.NICHE_WORD.get(niche, "design"),
        "city": city,
        "observation": (observation or "").strip(),
        "sender": config.SENDER_NAME,
    }


def first_email(f, niche):
    k = kind(niche)
    subject = config.EMAIL_SUBJECT[k].format(**f)
    body = config.EMAIL_1[k].format(**f)
    return subject, body


def followup_email(f, touch):
    tpl = config.EMAIL_2 if touch == 2 else config.EMAIL_3
    return tpl.format(**f)


def whatsapp(f):
    return config.WHATSAPP_1.format(**f)


def footer():
    return f"\n\n--\n{config.OPT_OUT}\n{config.SENDER_ADDRESS}"
