"""VLM への指示文と出力スキーマ。

様式(レイアウト)に依存しない汎用の指示にしている。発行元ごとの癖は、テンプレートの
「読取ヒント」と ROI(読取領域)で後から足す。
指示文に具体例の値を書くとモデルがそれを書き写すことがあるため、例示は語の種類に留める。
"""
from __future__ import annotations

from ..fields import PARTY_KEYS, Field

STR = {"type": "string"}

SYSTEM = ("あなたは注文書・発注書から情報を構造化抽出する専門アシスタントです。"
          "紙面を精密に読み取り、指定されたJSONだけを出力します。説明文は出力しません。")

ROLES = ["orderer", "recipient", "ship_to", "end_user", "other"]


def _obj(props: dict, max_items: int | None = None) -> dict:
    return {"type": "object", "properties": props, "required": list(props.keys()), "additionalProperties": False}


PARTY_SCHEMA = _obj({k: STR for k in PARTY_KEYS})

COMPANY_SCHEMA = _obj({
    "name": STR, "department": STR, "contact": STR, "address": STR, "tel": STR, "fax": STR,
    "role": {"type": "string", "enum": ROLES},
})


def _custom_props(fields: list[Field]) -> dict:
    return {f.key: STR for f in fields}


def _custom_rules(fields: list[Field], title: str) -> str:
    if not fields:
        return ""
    lines = [f"{title}(custom に入れる):"]
    for f in fields:
        desc = f.description or f"『{f.label}』の値"
        lines.append(f"  - {f.key}: {f.label}。{desc}")
    return "\n".join(lines) + "\n"


def _source_words(text_mode: bool) -> tuple[str, str]:
    return ("テキスト", "テキスト") if text_mode else ("画像", "ページ")


def _own_rule(own_names: list[str]) -> str:
    names = [n for n in own_names if str(n).strip()]
    if not names:
        return ("   注文を受ける側(宛先『〜御中』『〜様』)の会社は recipient。差出人・発注者欄・社印のある"
                "注文を出した側を orderer とする。\n")
    joined = " / ".join(f"『{n}』" for n in names)
    return (f"   {joined} は注文を受ける側(受注者)であり、発注元ではない → role は recipient。\n"
            "   差出人・発注者欄・社印のある、注文を出した側の会社を orderer とする。\n")


# ── 1. 書類全体(表題・登場する会社・注文番号・日付など) ─────────────────────
def header_schema(custom: list[Field]) -> dict:
    props = {
        "document_title": STR,
        "companies": {"type": "array", "items": COMPANY_SCHEMA, "maxItems": 12},
        "order_number": STR,
        "order_date": STR,
        "delivery_date": STR,
        "buyer_contact": STR,
        "ship_to": PARTY_SCHEMA,
        "end_user": STR,
        "customer_order_number": STR,
        "subtotal": STR,
        "tax": STR,
        "total": STR,
        "payment_terms": STR,
        "notes": STR,
    }
    if custom:
        props["custom"] = _obj(_custom_props(custom))
    return _obj(props)


def header_prompt(own_names: list[str], custom: list[Field], hint: str = "", text_mode: bool = False,
                  page_no: int = 1) -> str:
    src, page = _source_words(text_mode)
    custom_json = ',"custom":{' + ",".join(f'"{f.key}":""' for f in custom) + "}" if custom else ""
    return (
        f"この{src}は注文書(発注書)の{page_no}{page}目です。次の情報を読み取ってください。\n"
        "様式は発行元ごとに異なります。ラベルの語や位置から意味で判断してください。\n\n"
        "1) document_title: 用紙に大きく印刷された書類の表題を書いてあるとおりに1つ(上部にあることが多いが、"
        "左端の縦書きの場合もある)。宛名・社名・注意書きは表題ではない。無ければ空。\n"
        "2) companies: 紙面に登場する会社・組織を漏れなく列挙する(ヘッダ・差出人欄・宛先・社判・フッタ・"
        "届け先欄・エンドユーザー欄)。各社 name(会社名を正式表記で。支店・営業所・部署名は department へ)、"
        "contact(その会社の担当者の氏名)、address、tel、fax、role を入れる。\n"
        "   role: orderer=注文を出した側(発注元) / recipient=注文を受ける側(宛先) / ship_to=届け先 / "
        "end_user=最終利用者 / other=それ以外(運送会社・銀行等)。\n"
        + _own_rule(own_names) +
        "3) order_number: 発注元が採番した注文番号(注文番号/発注番号/注文No/手配番号 等のラベル)。"
        "FAX送信時刻・日付・ページ番号・電話番号は注文番号ではない。"
        "客先注番/ユーザー注番/貴社注番 は customer_order_number へ。\n"
        "4) order_date: 発行日(注文日)を書いてあるとおりに。\n"
        "5) delivery_date: 書類全体に対する希望納期・納入期日(明細ごとの納期しか無ければ空)。\n"
        "6) buyer_contact: 発注元の担当者の氏名(人名。会社名・部署名ではない)。\n"
        "7) ship_to: 『納入先/納品先/届け先/送り先/直送先』として明示された届け先(company, department, contact, "
        "address, tel, fax)。明示が無ければ全て空。\n"
        "8) end_user: 『エンドユーザー/E/U/最終ユーザー/ユーザー名』として明示された会社名。無ければ空。\n"
        "9) subtotal/tax/total: 小計・消費税・合計の金額(数字と記号のまま)。payment_terms: 支払条件。"
        "notes: 備考欄の文章。\n"
        + _custom_rules(custom, "10) 追加項目")
        + (f"\n発行元固有の注意:\n{hint.strip()}\n" if hint.strip() else "")
        + "\n紙面に無い項目は空文字にし、推測で埋めない。この説明文中の語をそのまま値として書き写さない。\n"
        '出力JSONのみ: {"document_title":"","companies":[{"name":"","department":"","contact":"","address":"",'
        '"tel":"","fax":"","role":"orderer"}],"order_number":"","order_date":"","delivery_date":"","buyer_contact":"",'
        '"ship_to":{"company":"","department":"","contact":"","address":"","tel":"","fax":""},"end_user":"",'
        '"customer_order_number":"","subtotal":"","tax":"","total":"","payment_terms":"","notes":""'
        + custom_json + "}"
    )


# ── 2. 明細 ────────────────────────────────────────────────────────────
def items_schema(custom: list[Field]) -> dict:
    props = {
        "order_number": STR, "raw_code": STR, "corrected_code": STR, "product_name": STR,
        "quantity": STR, "unit": STR, "unit_price": STR, "amount": STR, "due_date": STR, "notes": STR,
    }
    for f in custom:
        props[f.key] = STR
    return _obj({
        "order_numbers": {"type": "array", "items": STR, "maxItems": 10},
        "line_items": {"type": "array", "items": _obj(props), "maxItems": 200},
    })


def items_prompt(master_text: str, custom: list[Field], hint: str = "", text_mode: bool = False,
                 page_no: int = 1, region: bool = False) -> str:
    src, page = _source_words(text_mode)
    where = "明細表の部分を切り出した画像" if region else f"{page_no}{page}目"
    if master_text:
        code_rule = ("   型番は誤読しやすい(O↔0, I↔1↔l, S↔5, B↔8, Z↔2 等)。下の商品マスタに近い型番があれば"
                     "正規の表記に補正して corrected_code に入れる(無ければ raw_code と同じ値)。\n")
        master = "\n商品マスタ(コード␉品名␉単位␉標準単価):\n" + master_text + "\n"
    else:
        code_rule = "   corrected_code には誤読(O↔0, I↔1 等)を補正した型番を入れる。自信が無ければ raw_code と同じ値。\n"
        master = ""
    extra_keys = "".join(f',"{f.key}":""' for f in custom)
    custom_rule = ""
    if custom:
        custom_rule = "9) 追加の列(各行に入れる):\n" + "".join(
            f"  - {f.key}: {f.label}。{f.description or ''}\n" for f in custom)
    return (
        f"この{src}は注文書(発注書)の{where}です。明細表から各明細行を抽出してください。\n"
        "列名・順序・位置は発行元により異なります。一般に『商品コード/品番/型番/型式』『品名』『数量』『単位』"
        "『単価』『金額』『注文番号』『納期』等の列があります。\n\n"
        "厳守事項:\n"
        "1) quantity は数量列の数字を実際に読み取る(1と決めつけない)。unit(個/式/台/本/枚/箱 等)も読む。\n"
        "2) unit_price(単価)と amount(金額)は別の列。金額=単価×数量 になるはず。両方読み取る。\n"
        "3) raw_code は『商品コード/品番/型番/型式』列の製品型番を紙面どおりに入れる。"
        "『手配番号/発注番号/注文No/管理番号/伝票番号』は型番ではない → order_number へ。\n"
        + code_rule +
        "4) 送料・手数料の行があれば明細に含める。消費税・合計・小計・値引きの集計行は含めない。\n"
        f"5) この{page}に実在する明細行だけを、上から順に抽出する。明細が無ければ line_items は空配列。\n"
        "6) due_date: 各行の『納期/希望納期/指定納期』欄の日付を書いてあるとおりに(月日のみでも可)。\n"
        "7) order_number: 各行の『注文No/発注番号』列の値。『基本番号 行番号』のように空白で分かれていれば詰めて"
        "1つにする。先頭の0は省略しない。行ごとの番号が無ければ空のまま(作らない)。"
        "用紙上部に1つだけ書かれた代表の注文番号は order_numbers に入れる。"
        "客先注番・FAX送信時刻・日付・ページ番号・電話番号は注文番号ではない。\n"
        "8) 紙面に無い値は空文字。この説明文中の語をそのまま値として書き写さない。\n"
        + custom_rule
        + (f"\n発行元固有の注意:\n{hint.strip()}\n" if hint.strip() else "")
        + master
        + '\n出力JSONのみ: {"order_numbers":[],"line_items":[{"order_number":"","raw_code":"","corrected_code":"",'
          '"product_name":"","quantity":"","unit":"","unit_price":"","amount":"","due_date":"","notes":""'
        + extra_keys + "}]}"
    )


# ── 3. 発注元だけを狙う再読取(発注元が1社も取れなかった時) ─────────────────
ORDERER_SCHEMA = PARTY_SCHEMA


def orderer_prompt(own_names: list[str], text_mode: bool = False) -> str:
    src, _ = _source_words(text_mode)
    names = [n for n in own_names if str(n).strip()]
    excl = (" / ".join(f"『{n}』" for n in names) + " は注文を受ける側なので絶対に除外する。\n") if names else ""
    return (
        f"この{src}の注文書を発行した『発注元(注文を出した会社)』を1社だけ特定してください。\n"
        + excl +
        "宛名『〜御中』の会社ではなく、差出人・発注者欄・発注担当者印・社印のある“注文を出した会社”を選ぶ。\n"
        "company は正式表記の会社名、department は支店・営業所・部署、contact は担当者の氏名、address/tel/fax は併記された値。\n"
        '出力JSONのみ: {"company":"","department":"","contact":"","address":"","tel":"","fax":""}'
    )


# ── 4. ROI(切り出した領域の焦点読取) ───────────────────────────────────
VALUE_SCHEMA = _obj({"value": STR})


def roi_field_prompt(label: str, instruction: str = "") -> str:
    return (
        f"この画像は注文書の一部『{label}』の欄を切り出したものです。\n"
        + (instruction.strip() + "\n" if instruction.strip() else "")
        + "その値を書いてあるとおりに value へ入れてください。ラベルの語そのものは含めない。"
        "読めない・記載が無い場合は空文字。\n"
        '出力JSONのみ: {"value":""}'
    )


def roi_party_prompt(role_label: str, instruction: str = "") -> str:
    return (
        f"この画像は注文書の『{role_label}』の欄を切り出したものです。次を読み取ってください。\n"
        "company: 会社名(正式表記)。department: 支店・営業所・部署。contact: 担当者の氏名(人名)。"
        "address: 住所。tel / fax: 電話・FAX番号。\n"
        + (instruction.strip() + "\n" if instruction.strip() else "")
        + "記載が無い項目は空文字。\n"
        '出力JSONのみ: {"company":"","department":"","contact":"","address":"","tel":"","fax":""}'
    )


def with_text(prompt: str, text: str, limit: int = 12000) -> str:
    """テキスト文書(メール本文・Excel 等)を指示文に添える。"""
    t = (text or "").strip()
    if len(t) > limit:
        t = t[:limit] + "\n…(以下省略)"
    return prompt + "\n\n--- 書類のテキスト ---\n" + t
