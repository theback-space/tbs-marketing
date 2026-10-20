"""Deterministic compliance checks that run regardless of what the AI says.

Banned phrases and other people's contact details are always checked. The PHI,
patient-confirmation and emergency checks only apply in HIPAA mode (TBS_COMPLIANCE=hipaa),
for accounts that talk to patients. They are deliberately conservative and complement —
not replace — a practice's own HIPAA policies and review.
"""

from __future__ import annotations

import re

from tbs_social.config import Brand

# Signals that an inbound message contains (or invites) protected health information.
_PHI_PATTERNS = [
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"), "date (possible DOB or appointment date)"),
    (re.compile(r"\b(dob|date of birth|mrn|medical record|insurance (id|number)|member id)\b", re.I),
     "identifier keyword"),
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "possible SSN"),
    (re.compile(r"\b(\+?\d{1,2}\s?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "phone number"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "email address"),
    (re.compile(r"\b(my|our) (\w+ ){0,2}?(appointment|treatment|session|visit|diagnosis|x-?ray|mri|scan|"
                r"prescription|surgery|bill|invoice|records?|results?)\b", re.I),
     "references their own care"),
    (re.compile(r"\b(i|i've|i have|i was|i'm|i am) (been )?(diagnosed|prescribed|treated|seen|"
                r"referred|taking|having surgery)\b", re.I),
     "discloses personal health history"),
    (re.compile(r"\b(herniated|slipped disc|sciatica|scoliosis|fracture|stenosis|arthritis|"
                r"depress\w*|anxiety|pregnan\w*|cancer|diabet\w*|opioid|medication)\b", re.I),
     "mentions a medical condition"),
]

# Phrases in an outbound public reply that would confirm someone is a patient.
_PATIENT_CONFIRMATION = re.compile(
    r"\b(your (\w+ ){0,2}?(appointment|treatment|session|visit|diagnosis|records?|results?|scan|x-?ray|mri|"
    r"plan of care|care plan|progress|recovery|file|chart|bill|invoice|therapist|clinician)|"
    r"when we (saw|treated|examined) you|at your last|as your (clinician|therapist|provider)|"
    r"welcome back|great to see you again)\b",
    re.I,
)

_EMERGENCY = re.compile(
    r"\b(suicid\w*|kill myself|self[- ]harm|overdos\w*|can'?t (feel|move) my (legs?|arms?)|"
    r"loss of (bladder|bowel)|numb(ness)? in (my )?(groin|saddle)|chest pain|emergency)\b",
    re.I,
)


def scan_inbound(text: str) -> list[str]:
    """Return reasons an inbound comment/message may contain PHI (empty list = none found)."""
    return [label for pattern, label in _PHI_PATTERNS if pattern.search(text)]


def is_emergency(text: str) -> bool:
    return bool(_EMERGENCY.search(text))


def check_outbound(text: str, brand: Brand, *, is_reply: bool, hipaa: bool = False) -> list[str]:
    """Return compliance issues with text we are about to publish (empty list = clean)."""
    issues: list[str] = []
    lowered = text.lower()
    for phrase in brand.banned_phrases:
        if re.search(rf"\b{re.escape(phrase.lower())}\b", lowered):
            issues.append(f"uses banned phrase '{phrase}'")
    if is_reply:
        # Never repeat someone's personal identifiers or contact details back in public.
        own_contacts = {brand.contact_email.lower()} if brand.contact_email else set()
        always = {"email address", "phone number", "possible SSN", "identifier keyword"}
        for label in scan_inbound(text):
            if label == "email address" and own_contacts and all(
                    e.lower() in own_contacts for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)):
                continue
            if label == "mentions a medical condition":
                continue  # general education about conditions is fine in a reply
            if hipaa or label in always:
                issues.append(f"reply contains {label}")
    if not hipaa:
        return issues
    if is_reply and _PATIENT_CONFIRMATION.search(text):
        issues.append("public reply could confirm the person is a patient (HIPAA)")
    if re.search(r"\b(testimonial|patient story|our patient)\b", lowered):
        issues.append("references patients/testimonials — confirm written HIPAA authorization")
    return issues
