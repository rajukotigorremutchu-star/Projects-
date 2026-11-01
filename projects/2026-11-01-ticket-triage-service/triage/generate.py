"""Deterministic synthetic support-ticket generator.

Each ticket is built from a label-specific template plus shared "noise" phrases
and a small fraction of deliberately mislabeled tickets, so the task is not
trivially separable. ``template_id`` is recorded so evaluation can hold out
whole templates (see ``triage.model.split``).
"""
from __future__ import annotations

import random

import pandas as pd

TEMPLATES: dict[str, list[str]] = {
    "billing": [
        "I was charged twice for my {plan} subscription this month",
        "My invoice shows the wrong amount, please issue a refund",
        "Why did the price of my {plan} plan go up on my credit card statement",
        "Need a receipt for the payment I made on {day}",
        "Cancel my subscription and refund the last charge",
        "The coupon code was not applied to my bill",
    ],
    "bug": [
        "The app crashes every time I open the {feature} page",
        "I get an error 500 when I try to export my {feature}",
        "The {feature} button does nothing after the latest update",
        "Page freezes and shows a blank screen when loading {feature}",
        "Data is missing from the {feature} report, looks like a bug",
        "Upload fails with a timeout exception on {feature}",
    ],
    "account_access": [
        "I cannot log in, my password reset email never arrives",
        "My account is locked after too many sign in attempts",
        "Two factor code is not accepted when I sign in",
        "Please change the email address on my account, I lost access to the old one",
        "I forgot my username and cannot access my {plan} workspace",
        "Single sign on redirects me back to the login screen",
    ],
    "feature_request": [
        "It would be great if you added dark mode to the {feature} view",
        "Please add support for exporting {feature} to CSV",
        "Can you build an integration with Slack for notifications",
        "We would love a bulk edit option for {feature}",
        "Suggestion: allow custom fields in the {feature} module",
        "Any plans to offer an offline mode in the mobile app",
    ],
    "shipping": [
        "My order has not arrived and the tracking number shows no movement",
        "The package was delivered to the wrong address",
        "I received a damaged item and need a replacement shipped",
        "How long does delivery take to {day} for express shipping",
        "Tracking says delivered on {day} but nothing is at my door",
        "Order is missing one item from the box",
    ],
}
PLANS = ["basic", "pro", "team", "enterprise"]
FEATURES = ["dashboard", "reports", "inventory", "calendar", "billing history", "settings"]
DAYS = ["Monday", "Tuesday", "Friday", "the 3rd", "last week"]
OPENERS = ["", "Hi team, ", "Hello, ", "Urgent: ", "Good morning. ", "Hey, "]
CLOSERS = ["", " Thanks!", " Please help.", " This is really frustrating.", " Any update would be appreciated.", " Regards"]
LABELS = list(TEMPLATES)


def generate_tickets(n: int = 1500, seed: int = 7, label_noise: float = 0.05) -> pd.DataFrame:
    """Return ``n`` tickets with columns ticket_id, text, label, template_id."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= label_noise < 1:
        raise ValueError("label_noise must be in [0, 1)")
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        true_label = rng.choice(LABELS)
        t_idx = rng.randrange(len(TEMPLATES[true_label]))
        body = TEMPLATES[true_label][t_idx].format(
            plan=rng.choice(PLANS), feature=rng.choice(FEATURES), day=rng.choice(DAYS)
        )
        text = rng.choice(OPENERS) + body + rng.choice(CLOSERS)
        label = true_label
        if rng.random() < label_noise:
            label = rng.choice([l for l in LABELS if l != true_label])
        rows.append(
            {"ticket_id": f"T{i:05d}", "text": text, "label": label,
             "template_id": f"{true_label}:{t_idx}"}
        )
    return pd.DataFrame(rows)
