"""顧客マスタ照合(発注元・納品先 → 顧客コード)。

「注文書の文字列とマスタ文字列全体の類似度」で1件を選ぶ方式は、同じ会社の別レコード
(担当者違い・部署違い・支店違い)を取り違えやすい。ここではマスタ主導で照合する:
  1. 候補を会社で絞る(社名のコアが注文書に現れるレコード)
  2. 候補ごとに、そのレコード固有の情報(担当者・部署・TEL・〒・市区町村・番地)が注文書に
     現れるかを根拠として点数化する。注文書側は「役割の欄(primary: 発注元欄/納品先欄)」と
     「全文(secondary)」に分け、役割の欄の根拠を重く見る。希少な語ほど重い(IDF)。
  3. 同点付近は利用実績(過去に確定した書類)を優先する。
社名だけで他の根拠が無い時は確定せず空欄にする(誤った相手に紐づけるより人手が安全)。
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)
_CORP_RE = re.compile(
    r"株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|公益社団法人|公益財団法人|"
    r"国立研究開発法人|国立大学法人|学校法人|医療法人|社会福祉法人|独立行政法人|CO\.,?\s?LTD\.?|INC\.?|CORP\.?"
)
_SYM_RE = re.compile(r"[\s\-・･,.。、／/()（）「」『』【】\[\]〒:：;；'\"`~〜～*＊|｜#＃!！?？&＆]")
# 物流拠点・受入場所など「届け先」型のレコードを示す語
HUB_KEYWORDS = ("物流", "センター", "倉庫", "受入", "配送", "集荷", "検収")
_PERSON_STOP = {"ご担当者", "担当者", "御中", "様", "各位", "殿", "宛"}


def _nfkc(s) -> str:
    s = unicodedata.normalize("NFKC", "" if s is None else str(s))
    s = s.replace("(株)", "株式会社").replace("㈱", "株式会社").replace("(有)", "有限会社").replace("㈲", "有限会社")
    return re.sub(r"[ー―－—–‐−]", "-", s)


def norm(s) -> str:
    return _SYM_RE.sub("", _nfkc(s)).upper()


def company_core(s) -> str:
    """社名から会社種別を除いた同定用のコア。"""
    return norm(_CORP_RE.sub(" ", _nfkc(s).upper()))


def _digits(s) -> str:
    return re.sub(r"\D", "", _nfkc(s))


def _split_tokens(s) -> list[str]:
    stop = {norm(x) for x in _PERSON_STOP}
    out = []
    for t in re.split(r"[\s・･/／、,]+", _nfkc(s)):
        n = norm(t)
        if len(n) < 2 or n in stop or re.fullmatch(r"[A-Z]{1,2}", n):
            continue
        out.append(n)
    return out


_TEL_RE = re.compile(r"(?<!\d)\(?(\d{2,5})\)?[-\s]*(\d{1,4})[-\s]*(\d{3,4})(?!\d)")
_ZIP_RE = re.compile(r"(?<!\d)(\d{3})-?(\d{4})(?!\d)")


def _tels_in(raw: str) -> set[str]:
    s = _nfkc(raw)
    out = set()
    for m in _TEL_RE.finditer(s):
        d = "".join(m.groups())
        if 9 <= len(d) <= 11 and d.startswith("0"):
            out.add(d)
    out.update(m.group(0) for m in re.finditer(r"(?<!\d)0\d{9,10}(?!\d)", s))
    return out


def _zips_in(raw: str) -> set[str]:
    return {"".join(m.groups()) for m in _ZIP_RE.finditer(_nfkc(raw))}


@dataclass
class CustRec:
    code: str
    company: str
    dept: str = ""
    person: str = ""
    pref: str = ""
    city: str = ""
    addr: str = ""
    zip: str = ""
    tel: str = ""
    hidden: bool = False
    company_n: str = ""
    core: str = ""
    person_tokens: list[str] = field(default_factory=list)
    dept_tokens: list[str] = field(default_factory=list)
    addr_tokens: list[str] = field(default_factory=list)
    city_n: str = ""
    pref_n: str = ""
    text_n: str = ""
    order: int = 0

    def finalize(self) -> "CustRec":
        # 住所欄に都道府県・市区町村がまとめて入っているマスタにも対応する
        if not self.pref and self.addr:
            for p in PREFECTURES:
                if self.addr.startswith(p):
                    self.pref, self.addr = p, self.addr[len(p):]
                    break
        if not self.city and self.addr:
            m = re.match(r"(.+?[市区町村郡])", self.addr)
            if m and len(m.group(1)) <= 8:
                self.city, self.addr = m.group(1), self.addr[len(m.group(1)):]
        self.company_n = norm(self.company)
        self.core = company_core(self.company)
        self.person_tokens = _split_tokens(self.person)
        self.dept_tokens = _split_tokens(self.dept)
        self.addr_tokens = _split_tokens(self.addr)
        self.city_n = norm(self.city)
        self.pref_n = norm(self.pref)
        self.zip = _digits(self.zip)[:7] if self.zip else ""
        self.tel = _digits(self.tel)
        self.text_n = norm(" ".join([self.company, self.dept, self.person, self.pref, self.city, self.addr]))
        return self

    @property
    def is_hub(self) -> bool:
        blob = _nfkc(self.dept + " " + self.person + " " + self.addr)
        return any(k in blob for k in HUB_KEYWORDS)

    def label(self) -> str:
        return " ".join(p for p in (self.company, self.dept, self.person) if p)


@dataclass
class CustomerIndex:
    records: list[CustRec]
    idf: dict[str, float]

    def weight(self, tok: str) -> float:
        return self.idf.get(tok, 1.0)


def build_index(records: list[CustRec]) -> CustomerIndex:
    n = max(1, len(records))
    df: dict[str, int] = {}
    for r in records:
        for t in set(r.person_tokens + r.dept_tokens + r.addr_tokens + ([r.city_n] if r.city_n else [])):
            df[t] = df.get(t, 0) + 1
    logn = math.log(n + 1)
    idf = {t: min(1.0, max(0.15, math.log((n + 1) / (d + 1)) / logn)) for t, d in df.items()}
    return CustomerIndex(records, idf)


class DocEvidence:
    """primary = 役割に対応する欄のテキスト、secondary = 書類全文。"""

    def __init__(self, primary: str, secondary: str = ""):
        self.primary_raw, self.secondary_raw = str(primary or ""), str(secondary or "")
        self.primary, self.secondary = norm(self.primary_raw), norm(self.secondary_raw)
        self.all = self.primary + "|" + self.secondary
        self.primary_core = company_core(self.primary_raw)
        self.all_core = self.primary_core + "|" + company_core(self.secondary_raw)
        self.p_tels, self.s_tels = _tels_in(self.primary_raw), _tels_in(self.secondary_raw)
        self.p_zips, self.s_zips = _zips_in(self.primary_raw), _zips_in(self.secondary_raw)
        self.primary_tokens = [t for t in _split_tokens(self.primary_raw) if not t.isdigit() or len(t) >= 3]


def company_hit(rec: CustRec, ev: DocEvidence) -> float:
    c = rec.core
    if not c:
        return 0.0
    if len(c) < 3:   # 短い社名は偶然の一致が多いので役割の欄に限る
        return 1.0 if (c in ev.primary or c in ev.primary_core) else 0.0
    if c in ev.all or c in ev.all_core:
        return 1.0
    if len(c) >= 6:
        pre = c[: max(4, int(len(c) * 0.6))]
        if pre in ev.all or pre in ev.all_core:
            return 0.6
    return 0.0


def _is_person_like(t: str) -> bool:
    return 3 <= len(t) <= 5 and not re.search(r"[A-Za-z0-9]", t)


def _tok_hits(tokens: list[str], ev: DocEvidence, idx: CustomerIndex, base: float, partial: bool = False):
    s, hits, full = 0.0, [], 0
    for t in tokens:
        w = base * idx.weight(t)
        if t in ev.primary:
            s += w; hits.append(t); full += 1
        elif t in ev.secondary:
            s += 0.5 * w; hits.append(t + "*"); full += 1
        elif partial and _is_person_like(t) and t[:2] in ev.all:
            s += 0.35 * w; hits.append(t[:2] + "…")   # 姓だけの一致(名の誤読救済)
    return s, hits, full


def score_record(rec: CustRec, ev: DocEvidence, idx: CustomerIndex, hist_count: int = 0) -> tuple[float, str, int]:
    ch = company_hit(rec, ev)
    if ch <= 0:
        return 0.0, "社名なし", 0
    s = 3.0 * ch + (0.5 if (rec.core in ev.primary or rec.core in ev.primary_core) else 0.0)
    why = [f"社名{ch:.1f}"]
    # 部署と担当者はほぼ同じ重み(部署で分かれるマスタと担当者で分かれるマスタの両方がある)
    ps, ph, _ = _tok_hits(rec.person_tokens, ev, idx, 2.2, partial=True)
    ds, dh, dfull = _tok_hits(rec.dept_tokens, ev, idx, 2.0)
    as_, ah, _ = _tok_hits(rec.addr_tokens, ev, idx, 1.0)
    s += ps + ds + as_
    if ph: why.append("担当" + "/".join(ph))
    if dh: why.append("部署" + "/".join(dh))
    if ah: why.append("住所" + "/".join(ah))
    if rec.tel:
        if rec.tel in ev.p_tels:
            s += 2.0; why.append("TEL")
        elif rec.tel in ev.s_tels:
            s += 1.0; why.append("TEL*")
        elif len(rec.tel) >= 9 and any(t.endswith(rec.tel[-8:]) for t in ev.p_tels | ev.s_tels):
            s += 0.7; why.append("TEL下8桁")
    if rec.zip:
        if rec.zip in ev.p_zips:
            s += 1.0; why.append("〒")
        elif rec.zip in ev.s_zips:
            s += 0.5; why.append("〒*")
    if rec.city_n:
        if rec.city_n in ev.primary:
            s += 0.8; why.append("市区")
        elif rec.city_n in ev.secondary:
            s += 0.4
    if rec.pref_n and rec.pref_n in ev.all:
        s += 0.2
    rev = [t for t in ev.primary_tokens if len(t) >= 2 and t in rec.text_n and t not in rec.core]
    if rev:
        s += min(1.5, 0.3 * len(rev)); why.append("逆" + "/".join(rev[:4]))
    if rec.hidden:
        s -= 0.5; why.append("非表示")
    if hist_count:
        s += 0.2 * min(math.log1p(hist_count), 4.0)
    return s, " ".join(why), dfull


@dataclass
class Resolution:
    code: str
    score: float
    rec: CustRec
    why: str
    runner_up: str = ""


def _same_company(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.startswith(short)


def _pick(cands: list[CustRec], ev: DocEvidence, idx: CustomerIndex, hist: dict[str, int], extra=None,
          dept_penalty: float = 2.0, hist_margin: float = 0.0, pref_hist: dict[str, int] | None = None):
    scored = []
    dept_matched = False
    for r in cands:
        s, why, dfull = score_record(r, ev, idx, hist.get(r.code, 0))
        if s <= 0:
            continue
        dept_matched = dept_matched or bool(dfull)
        if extra:
            adj, tag = extra(r)
            s += adj
            if tag:
                why += " " + tag
        scored.append([s, r, why, dfull, bool(r.dept_tokens)])
    if not scored:
        return None
    # 部署の整合: どれかの候補の部署が書類に現れているなら、部署を持つのに1語も現れない候補は下げる
    if dept_matched and dept_penalty:
        for row in scored:
            if row[4] and not row[3]:
                row[0] -= dept_penalty
    scored.sort(key=lambda t: (-t[0], -hist.get(t[1].code, 0), -t[1].order))
    best = scored[0]
    if hist_margin > 0 and len(scored) > 1:
        near = [t for t in scored if t[0] >= best[0] - hist_margin and _same_company(t[1].core, best[1].core)]
        if len(near) > 1:
            ph = pref_hist or {}

            def rank(t):
                return (ph.get(t[1].code, 0), hist.get(t[1].code, 0), t[0], t[1].order)
            cand = max(near, key=rank)
            if cand[1].code != best[1].code and rank(cand) > rank(best):
                best = [cand[0], cand[1], cand[2] + " 実績優先", cand[3], cand[4]]
    ru = f"{scored[1][1].code}:{scored[1][0]:.2f}" if len(scored) > 1 else ""
    return Resolution(best[1].code, best[0], best[1], best[2], ru)


def resolve(ev: DocEvidence, idx: CustomerIndex, hist: dict[str, int], *, role: str = "ship_to",
            exclude_codes: set[str] | None = None, exclude_cores: list[str] | None = None,
            buyer_core: str = "", min_score: float = 3.9, hist_margin: float = 2.0,
            pref_hist: dict[str, int] | None = None, contact: str = "") -> Resolution | None:
    """役割の欄(ev.primary)の根拠で顧客レコードを1件選ぶ。根拠不足なら None。

    role="buyer": 発注元。発注元欄ははっきりしているので部署の不一致を強く効かせ、
                  物流拠点型レコードは下げる。担当者が誰とも一致しない時は担当者なしの汎用レコードを優先。
    role="ship_to": 納品先。発注元と同じ会社のレコードは拠点型(物流センター等)だけを候補にする
                  (書類に載る発注元の請求先・購買部門を納品先として拾わないため)。
    """
    ex_codes = exclude_codes or set()
    ex_cores = [company_core(c) for c in (exclude_cores or []) if company_core(c)]
    cands = []
    for r in idx.records:
        if r.code in ex_codes or not r.core or any(x in r.core or r.core in x for x in ex_cores):
            continue
        if role == "ship_to" and buyer_core and (r.core == buyer_core or r.core in buyer_core or buyer_core in r.core) \
                and not r.is_hub:
            continue
        cands.append(r)
    if role == "buyer":
        contact_n = norm(contact)
        any_person_hit = any(t in ev.all for r in cands if company_hit(r, ev) > 0 for t in r.person_tokens)

        def extra(r: CustRec):
            adj, tags = 0.0, []
            if r.is_hub:
                adj -= 1.5; tags.append("拠点型")
            if contact_n and not any_person_hit and not r.person_tokens:
                adj += 0.8; tags.append("汎用")
            return adj, " ".join(tags)
        res = _pick(cands, ev, idx, hist, extra, dept_penalty=4.0, hist_margin=0.0)
    else:
        res = _pick(cands, ev, idx, hist, hist_margin=hist_margin, pref_hist=pref_hist)
    if res is None or res.score < min_score:
        return None
    return res
