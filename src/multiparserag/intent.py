import re

from .models import ContextRequest, UserIntent

_SMALL_TALK = {
    "hello",
    "hello there",
    "hey",
    "hi",
    "hi there",
    "good morning",
    "good afternoon",
    "good evening",
    "thank you",
    "thanks",
    "thanks a lot",
    "bye",
    "goodbye",
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "早上好",
    "下午好",
    "晚上好",
    "谢谢",
    "谢谢你",
    "多谢",
    "再见",
}
_PUNCTUATION = " \t\r\n,.!?;:~，。！？；：～、"
_POLITE_PREFIX = re.compile(
    r"^(?:(?:请问?|麻烦(?:你)?|帮我)[\s，,、]*|(?:please|can you|could you)\b[\s,]+)+",
    re.I,
)
_SUMMARY_PREFIX = re.compile(
    r"^(?:(?:总结|概括|概述|归纳|简述)(?:一下)?|summari[sz]e\b|"
    r"(?:give me )?(?:an overview|a summary) of\b)",
    re.I,
)
_COMPARE_PREFIX = re.compile(
    r"^(?:对比(?:一下)?|比较(?:一下|(?=\s|[:：]|.+[与和跟及、].+))|compare\b)", re.I
)
_COMPARE_CUE = re.compile(
    r"有(?:什么|何)(?:主要)?(?:区别|差别|不同)|(?:差异|区别)(?:是什么|在哪)|哪个更|"
    r"\b(?:differences? between|versus)\b|\bvs\b",
    re.I,
)
_PROCEDURE_CUE = re.compile(
    r"^(?:如何|怎么|怎样)|(?:步骤|流程)(?:是什么|有哪些)|"
    r"^how (?:to|do (?:i|we)|can (?:i|we)|should (?:i|we))\b|\bsteps (?:to|for)\b",
    re.I,
)


def resolve_intent(request: ContextRequest) -> tuple[UserIntent, str | None]:
    query = request.query
    directive = _POLITE_PREFIX.sub("", query)
    summarize = _SUMMARY_PREFIX.match(directive)
    compare = _COMPARE_PREFIX.match(directive)
    intent = request.intent
    if intent == "auto":
        greeting = " ".join(query.casefold().split()).strip(_PUNCTUATION)
        if greeting in _SMALL_TALK:
            intent = "small_talk"
        elif summarize:
            intent = "summarize"
        elif compare or _COMPARE_CUE.search(directive):
            intent = "compare"
        elif _PROCEDURE_CUE.search(directive):
            intent = "how_to"
        else:
            intent = "lookup"

    if intent == "small_talk":
        return intent, None
    prefix = {"summarize": summarize, "compare": compare}.get(intent)
    if prefix:
        subject = directive[prefix.end() :].lstrip(" \t\r\n:：,，、")
        if subject.strip(_PUNCTUATION):
            return intent, subject
    return intent, query
