"""Patient-facing companion text: reviewed templates only, Sinhala and English (SAF-1, API-22).

Every reply ends in navigation (a question, a specialty, a doctor, a booking or a safety step).
The urgency sentences are the spec 07 section 2 wording. Sinhala text is a draft for the native
speaker review (issue D05); `safety` will own the final copy (`modules/safety/content/si.yaml`).
"""

from __future__ import annotations

from suwapath.contracts.enums import Language, Specialty, Urgency

T = dict[Language, str]
EN, SI = Language.EN, Language.SI

URGENCY: dict[Urgency, T] = {
    Urgency.EMERGENCY: {
        EN: "This may be an emergency. Call 1990 now.",
        SI: "මෙය හදිසි තත්ත්වයක් විය හැක. දැන්ම 1990 අමතන්න.",
    },
    Urgency.URGENT_24H: {
        EN: "Please see a doctor today.",
        SI: "කරුණාකර අදම වෛද්‍යවරයෙකු හමුවන්න.",
    },
    Urgency.SOON_72H: {
        EN: "Please see a doctor within 3 days.",
        SI: "කරුණාකර දින 3ක් ඇතුළත වෛද්‍යවරයෙකු හමුවන්න.",
    },
    Urgency.ROUTINE: {
        EN: "Please see a doctor within 2 weeks.",
        SI: "කරුණාකර සති 2ක් ඇතුළත වෛද්‍යවරයෙකු හමුවන්න.",
    },
}

TEXT: dict[str, T] = {
    "emergency.can_you_call": {
        EN: "Are you able to call 1990 now?",
        SI: "ඔබට දැන් 1990 ඇමතිය හැකිද?",
    },
    "emergency.guardian_told": {
        EN: "Your family contact is being told.",
        SI: "ඔබගේ පවුලේ සම්බන්ධතාවයට දැනුම් දෙමින් සිටී.",
    },
    "check_in.ask": {
        EN: "Hello. Some of your tablets were not marked as taken. Are you feeling all right?",
        SI: "ආයුබෝවන්. ඔබේ බෙහෙත් සමහරක් ගත් බව සටහන් වී නැහැ. ඔබට සනීපද?",
    },
    "check_in.thanks": {
        EN: "Thank you. If you feel unwell, tell me what you feel and I will help you find care.",
        SI: "ස්තූතියි. ඔබට අපහසුවක් දැනේ නම්, දැනෙන දේ මට කියන්න. මම ප්‍රතිකාර සොයා ගැනීමට උදව් කරන්නම්.",
    },
    "clarify": {
        EN: "Sorry, I did not understand. Please choose one of the answers below.",
        SI: "සමාවෙන්න, මට තේරුණේ නැහැ. කරුණාකර පහත පිළිතුරකින් එකක් තෝරන්න.",
    },
    "route.specialty": {
        EN: "A {specialty} doctor is the right kind of doctor to see.",
        SI: "ඔබට හමුවිය යුත්තේ {specialty} වෛද්‍යවරයෙකි.",
    },
    "route.doctor": {
        EN: "{doctor} at {place} has a time on {time}. Tap below to book it.",
        SI: "{place} හි {doctor} හට {time} වේලාවක් ඇත. වෙන්කරවා ගැනීමට පහත තට්ටු කරන්න.",
    },
    "route.no_doctor": {
        EN: "Tap below to see doctors you can book.",
        SI: "වෙන්කරවා ගත හැකි වෛද්‍යවරුන් බැලීමට පහත තට්ටු කරන්න.",
    },
    "after_result": {
        EN: "I can book a doctor for you, or show you other doctors.",
        SI: "මට ඔබට වෛද්‍යවරයෙකු වෙන්කරවා දීමට, හෝ වෙනත් වෛද්‍යවරුන් පෙන්වීමට හැක.",
    },
    "question.generic_yes_no": {
        EN: "Are you having {concept}?",
        SI: "ඔබට {concept} තිබේද?",
    },
    "question.generic_value": {
        EN: "Please tell me about {concept}.",
        SI: "කරුණාකර {concept} ගැන මට කියන්න.",
    },
}

# Question texts by navigation question key. The knowledge base (D08) adds the full catalogue.
QUESTIONS: dict[str, T] = {
    "q.dizziness_since": {
        EN: "Since when have you felt dizzy?",
        SI: "ඔබට කරකැවිල්ල දැනෙන්නේ කවදා සිට ද?",
    },
    "q.fever": {EN: "Is there any fever?", SI: "ඔබට උණ තිබේද?"},
    "q.fever_days": {
        EN: "For how many days have you had the fever?",
        SI: "ඔබට උණ තිබෙන්නේ දින කීයක සිට ද?",
    },
    "q.chest_pain": {EN: "Is there pain in your chest?", SI: "ඔබේ පපුවේ වේදනාවක් තිබේද?"},
    "q.face_weakness": {
        EN: "Is one side of your face weak or drooping?",
        SI: "ඔබේ මුහුණේ එක් පැත්තක් දුර්වල හෝ එල්ලා වැටී තිබේද?",
    },
    "q.fall": {EN: "Have you fallen recently?", SI: "ඔබ මෑතකදී වැටුණාද?"},
}

CHIPS: dict[str, T] = {
    "yes": {EN: "Yes", SI: "ඔව්"},
    "no": {EN: "No", SI: "නැහැ"},
    "unsure": {EN: "Not sure", SI: "විශ්වාස නැහැ"},
    "call_1990": {EN: "Call 1990", SI: "1990 අමතන්න"},
    "call_guardian": {EN: "Call my family", SI: "පවුලේ අයට අමතන්න"},
    "book": {EN: "Book {time}", SI: "{time} වෙන් කරන්න"},
    "show_doctors": {EN: "Show other doctors", SI: "වෙනත් වෛද්‍යවරුන්"},
    "feel_ok": {EN: "I feel all right", SI: "මට සනීපයි"},
    "not_well": {EN: "I am not well", SI: "මට අසනීපයි"},
}

SPECIALTY: dict[Specialty, T] = {
    Specialty.GENERAL_MEDICINE: {EN: "general medicine", SI: "සාමාන්‍ය වෛද්‍ය"},
    Specialty.CARDIOLOGY: {EN: "heart (cardiology)", SI: "හෘද රෝග"},
    Specialty.ENDOCRINOLOGY: {EN: "diabetes and hormone", SI: "දියවැඩියා සහ හෝමෝන"},
    Specialty.NEUROLOGY: {EN: "nerve and brain (neurology)", SI: "ස්නායු රෝග"},
    Specialty.RESPIRATORY: {EN: "chest and lung", SI: "පපුව සහ පෙනහළු"},
    Specialty.PAEDIATRICS: {EN: "child (paediatrics)", SI: "ළමා රෝග"},
    Specialty.OBSTETRICS_GYNAECOLOGY: {
        EN: "pregnancy and women's health",
        SI: "ප්‍රසව හා නාරි රෝග",
    },
}


def text(key: str, language: Language, **params: str) -> str:
    return TEXT[key][language].format(**params)


def chip(key: str, language: Language, **params: str) -> str:
    return CHIPS[key][language].format(**params)[:40]


def specialty_name(specialty: Specialty, language: Language) -> str:
    names = SPECIALTY.get(specialty)
    if names is None:
        return specialty.value.replace("_", " ")
    return names[language]


def question_text(key: str, concept: str, answer_type: str, language: Language) -> str:
    known = QUESTIONS.get(key)
    if known is not None:
        return known[language]
    label = concept.replace("_", " ")
    generic = "question.generic_yes_no" if answer_type == "yes_no" else "question.generic_value"
    return text(generic, language, concept=label)


def all_texts() -> list[tuple[str, Language, str]]:
    """Every fixed template, for the wording test (no diagnosis, no reassurance, no dosing)."""
    rows = [(f"urgency.{u.value}", lang, t) for u, ts in URGENCY.items() for lang, t in ts.items()]
    for group, table in (("text", TEXT), ("question", QUESTIONS), ("chip", CHIPS)):
        rows += [(f"{group}.{k}", lang, t) for k, ts in table.items() for lang, t in ts.items()]
    return rows
