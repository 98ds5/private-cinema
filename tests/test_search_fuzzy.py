"""
模糊搜索回归锁: 打分模块 (app/utils/search.py) + 服务层集成 (StatsService.get_library_list)。

覆盖五档打分: 完全相等 / 前缀 / 子串 / 子序列(跳字) / 相似度容错(错别字) + 拼音层;
归一化 (大小写、空白、中文标点、全角); 短查询的噪声保护;
以及"过滤掉匹配"之后分类过滤、排序、分页是否仍然正确。
"""
import pytest

from app.database import get_session, init_database
from app.models.tables import Media
from app.services.stats import StatsService
from app.utils.search import (
    SCORE_EXACT, SCORE_PREFIX, SCORE_SUBSTRING,
    SCORE_PINYIN_EXACT, SCORE_PINYIN_PREFIX, SCORE_PINYIN_SUBSTRING,
    match_score, normalize, pinyin_key,
)


def test_normalize_strips_case_space_punct_and_fullwidth():
    assert normalize("  Cyber-Punk  ") == "cyberpunk"
    assert normalize("ＡＢ：Ｃ") == "abc"                     # NFKC: 全角 -> 半角
    assert normalize("海绵宝宝：深海大冒险") == "海绵宝宝深海大冒险"
    assert normalize("") == ""
    assert normalize(None) == ""


class TestScoreTiers:

    def test_exact_prefix_substring(self):
        assert match_score("星际穿越", "星际穿越") == SCORE_EXACT
        assert match_score("星际", "星际穿越") == SCORE_PREFIX
        assert match_score("穿越", "星际穿越") == SCORE_SUBSTRING

    def test_normalized_substring_beats_raw_like(self):
        """空格/标点差异: 归一化后命中 (旧的 SQL LIKE '%cyber punk%' 命中不了)"""
        assert match_score("cyber punk", "Cyberpunk Edgerunners") == SCORE_PREFIX
        assert match_score("海绵宝宝深海", "海绵宝宝：深海大冒险") == SCORE_PREFIX

    def test_subsequence_allows_skipping_chars(self):
        assert match_score("星穿", "星际穿越") > 0
        assert match_score("星穿", "星际穿越") < SCORE_SUBSTRING   # 跳字必然弱于连续子串

    def test_typo_tolerated_by_ratio(self):
        assert match_score("星际穿跃", "星际穿越") > 0
        assert match_score("Intersteller", "Interstellar") > 0

    def test_unrelated_is_zero(self):
        assert match_score("zzz", "星际穿越") == 0
        assert match_score("星际穿越", "") == 0
        assert match_score("", "星际穿越") == 0

    def test_short_query_no_fuzzy_noise(self):
        """单字只走子串档; 两字跨距离跳字要挡住, 否则到处误命中"""
        assert match_score("星", "星际穿越") == SCORE_PREFIX
        assert match_score("in", "Cyberpunk Edgerunners") == 0

    def test_case_and_fullwidth_insensitive(self):
        assert match_score("INTER", "Interstellar") > 0
        assert match_score("ｃｙｂｅｒ", "Cyberpunk Edgerunners") > 0


@pytest.fixture
def svc(tmp_path):
    """独立临时库 + 与真实片源风格一致的标题 (不碰真 data/cinema.db)"""
    init_database(str(tmp_path / "cinema.db"))
    with get_session() as s:
        for title, kind in (
            ("星际穿越", "movie"),
            ("Interstellar", "movie"),
            ("Cyberpunk Edgerunners", "anime"),
            ("海绵宝宝：深海大冒险", "movie"),
            ("泰迪熊1", "movie"),
            ("泰迪熊2", "movie"),
        ):
            s.add(Media(title=title, media_type=kind, year=2014,
                        status="unwatched"))
        s.commit()
    return StatsService()


def _titles(result):
    return [i["title"] for i in result["items"]]


class TestServiceIntegration:

    def test_fuzzy_queries_hit(self, svc):
        """旧的 LIKE 命中不了的三种输入, 现在都要能搜到"""
        assert _titles(svc.get_library_list(search="星穿")) == ["星际穿越"]
        assert _titles(svc.get_library_list(search="星际穿跃")) == ["星际穿越"]
        assert _titles(svc.get_library_list(search="cyber punk")) == \
            ["Cyberpunk Edgerunners"]

    def test_no_match_returns_empty(self, svc):
        r = svc.get_library_list(search="zzz不存在的片子")
        assert r["total"] == 0
        assert r["items"] == []

    def test_media_type_filter_still_applies(self, svc):
        """模糊匹配不许突破分类过滤: 电影页搜不到动漫, 反之亦然"""
        assert svc.get_library_list(media_type="movie",
                                    search="cyber punk")["total"] == 0
        assert _titles(svc.get_library_list(media_type="anime",
                                            search="cyber punk")) == \
            ["Cyberpunk Edgerunners"]

    def test_sort_and_pagination_survive_filtering(self, svc):
        """过滤之后: 总数、用户选的排序、分页切片都要照常工作"""
        r = svc.get_library_list(search="泰迪", sort="名称", limit=1)
        assert r["total"] == 2, "关键词命中 2 部, total 应是过滤后的总数"
        assert _titles(r) == ["泰迪熊1"], "第一页应是排序后的第 1 部"

        r2 = svc.get_library_list(search="泰迪", sort="名称", limit=10)
        assert _titles(r2) == ["泰迪熊1", "泰迪熊2"]

    def test_empty_search_lists_everything(self, svc):
        """空关键词 = 不过滤 (不能被当成"零结果")"""
        assert svc.get_library_list(search=None)["total"] == 6
        assert svc.get_library_list(search="")["total"] == 6

    def test_pinyin_query_hits_chinese_titles(self, svc):
        """拼音全拼 / 首字母缩写 / 带数字的缩写都要能搜到中文片名"""
        assert _titles(svc.get_library_list(search="xjcy")) == ["星际穿越"]
        assert _titles(svc.get_library_list(search="xingji")) == ["星际穿越"]
        assert _titles(svc.get_library_list(search="tdx2")) == ["泰迪熊2"]
        assert _titles(svc.get_library_list(search="hmbbshdmx")) == \
            ["海绵宝宝：深海大冒险"]


class TestPinyin:
    """拼音层: ASCII 输入命中中文标题 (可选依赖 pypinyin)"""

    @pytest.fixture(autouse=True)
    def _need_pypinyin(self):
        pytest.importorskip("pypinyin")

    def test_pinyin_key(self):
        assert pinyin_key("星际穿越") == ("xingjichuanyue", "xjcy")
        assert pinyin_key("泰迪熊2") == ("taidixiong2", "tdx2")
        assert pinyin_key("Cyberpunk Edgerunners") is None   # 无汉字 -> 不走拼音层
        assert pinyin_key("") is None

    def test_exact_and_prefix(self):
        assert match_score("xingjichuanyue", "星际穿越") == SCORE_PINYIN_EXACT
        assert match_score("xjcy", "星际穿越") == SCORE_PINYIN_EXACT
        assert match_score("xingji", "星际穿越") == SCORE_PINYIN_PREFIX
        assert match_score("xjc", "星际穿越") == SCORE_PINYIN_PREFIX

    def test_substring_of_full_pinyin(self):
        assert match_score("chuanyue", "星际穿越") == SCORE_PINYIN_SUBSTRING

    def test_initials_may_skip_one_letter(self):
        """缩写允许漏 1 个字母 ("星穿"=xc), 但必须从第一个字母开始"""
        assert 0 < match_score("xc", "星际穿越") < SCORE_PINYIN_PREFIX
        assert match_score("jy", "星际穿越") == 0        # 不从开头 -> 挡掉
        assert match_score("xy", "星际穿越") == 0        # 漏 2 个字母 -> 挡掉

    def test_pinyin_typo_tolerated(self):
        """全拼打错一个字母 (门槛比中文严: 0.8)"""
        assert 0 < match_score("xingjichuanyo", "星际穿越") < SCORE_PINYIN_PREFIX

    def test_short_ascii_does_not_match_pinyin_substring(self):
        """2 个字母不许做全拼子串, 否则 "in" 会命中 xingji..."""
        assert match_score("in", "星际穿越") == 0
        assert match_score("zzz", "星际穿越") == 0
        assert match_score("xyz", "星际穿越") == 0

    def test_chinese_input_still_wins(self):
        """中文直接命中优先于拼音命中, 中文档分数不变"""
        assert match_score("星际穿越", "星际穿越") == SCORE_EXACT
        assert match_score("星际", "星际穿越") == SCORE_PREFIX
        assert match_score("星穿", "星际穿越") > 0
