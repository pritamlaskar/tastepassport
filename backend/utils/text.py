import re


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"[^\x00-\x7F\u0080-\uFFFF]", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()

    return text


def truncate_for_extraction(text: str, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text

    cutoff = text[:max_chars]
    last_sentence = max(
        cutoff.rfind(". "),
        cutoff.rfind("! "),
        cutoff.rfind("? "),
        cutoff.rfind("\n"),
    )
    if last_sentence > max_chars * 0.7:
        return cutoff[:last_sentence + 1]

    return cutoff
