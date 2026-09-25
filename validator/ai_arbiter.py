import ai_client

SYSTEM = (
    "Decide whether two records describe the SAME real-world card promotion from the same bank "
    "(same exact card product and same offer mechanics; wording/language may differ). "
    "Do not treat an expanded marketing label or descriptive material/form wording as a different product merely "
    "because one name contains extra words. If one name is plausibly a longer label for the other and offer "
    "mechanics/reward align, that favors SAME. "
    "However, distinct products that are explicitly marketed as separate colour/edition/tier variants, "
    "personal/business variants, or otherwise separately named choices are DIFFERENT promotions even when reward "
    "and dates are identical. Judge product identity from the records, not from one extra token alone. "
    "A missing card_name may still match only when the remaining evidence strongly identifies one existing offer. "
    'Return {"same": true|false, "reason": "<short>"}.'
)

_FIELDS = ("card_name", "campaign_title", "campaign_type", "offer_summary", "reward_value", "conditions",
           "start_date", "end_date")


def _describe(row: dict) -> str:
    return "\n".join(f"{f}: {row.get(f)}" for f in _FIELDS)


def same_campaign(a: dict, b: dict) -> bool:
    try:
        result = ai_client.chat_json(SYSTEM, f"Record A:\n{_describe(a)}\n\nRecord B:\n{_describe(b)}")
    except Exception:
        return False
    return bool(isinstance(result, dict) and result.get("same") is True)
