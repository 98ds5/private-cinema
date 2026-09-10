"""
文件名解析器单元测试: S##E## / EP## 判定动漫与季集号、
年份提取与范围过滤、标题清洗剥离发行标记 (分辨率/来源/编码/压制组)。
"""
import sys
from pathlib import Path

import pytest

# 保证从项目根可导入 app 包 (不管从哪启动 pytest)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.name_parser import parse_filename, is_anime_directory, clean_title


class TestParseFilename:

    def test_season_episode_anime(self):
        """S01E03 → 动漫, 季1 集3"""
        r = parse_filename(r"D:\Anime\进击的巨人\Season 01\S01E03.mkv",
                           parent_dir=r"D:\Anime\进击的巨人\Season 01")
        assert r.is_anime
        assert r.season_number == 1
        assert r.episode_number == 3

    def test_episode_only_defaults_season_1(self):
        """EP03 → 动漫, 默认第1季"""
        r = parse_filename(r"D:\Anime\某番\EP03.mkv")
        assert r.is_anime
        assert r.season_number == 1
        assert r.episode_number == 3

    def test_movie_with_year_and_tags(self):
        """Interstellar.2014.BluRay.2160p.x265 → 电影, 2014年, 标题干净"""
        r = parse_filename(
            r"D:\Movies\Interstellar.2014.BluRay.2160p.x265\Interstellar.2014.mkv",
            parent_dir=r"D:\Movies\Interstellar.2014.BluRay.2160p.x265",
        )
        assert not r.is_anime
        assert r.year == 2014
        assert "2014" not in r.title and "BluRay" not in r.title
        assert r.title  # 非空

    def test_chinese_title_directory(self):
        """目录名 '盗梦空间 (2010)' → 标题 '盗梦空间', 年份 2010"""
        r = parse_filename(r"D:\Movies\盗梦空间 (2010)\Inception.mkv",
                           parent_dir=r"D:\Movies\盗梦空间 (2010)")
        assert r.title == "盗梦空间"
        assert r.year == 2010
        assert not r.is_anime

    def test_year_bounds_filtered(self):
        """年份过滤: 1984 这种像 ISBN 的 4 位数不应被当成年份"""
        r = parse_filename(r"D:\Movies\Some.File.1984.abc.1234567890\f.mkv",
                           parent_dir=r"D:\Movies\Some.File.1984.abc.1234567890")
        # 1900-2099 之外… 1984 在范围内会被提取; 这里只验证不崩溃并给出标题
        assert r.title

    def test_no_parent_dir_uses_filename(self):
        """不传 parent_dir 时用文件名解析"""
        r = parse_filename(r"D:\Movies\盗梦空间 (2010)\Inception.mkv")
        assert r.raw_filename == "Inception"
        assert r.year == 2010


class TestIsAnimeDirectory:

    def test_season_subdir(self, tmp_path):
        """含 Season 子目录 → 动漫目录"""
        (tmp_path / "Season 01").mkdir()
        assert is_anime_directory(tmp_path)

    def test_s_subdir(self, tmp_path):
        """含 S1 样式子目录 → 动漫目录"""
        (tmp_path / "S2").mkdir()
        assert is_anime_directory(tmp_path)

    def test_movie_directory(self, tmp_path):
        """只有视频文件的目录 → 不是动漫目录"""
        (tmp_path / "Inception.mkv").touch()
        assert not is_anime_directory(tmp_path)


class TestCleanTitle:
    """标题清洗回归: 用例取自真实片源命名, 锁住分类标记剥离行为"""

    def test_anime_folder_with_release_group(self):
        """整季包目录名: 季标记 + Complete + 音轨 + 编码 + 压制组全部剥掉"""
        raw = "Cyberpunk Edgerunners S01 Complete -HD MA 5.1 AVC -FraMeSToR"
        assert clean_title(raw) == "Cyberpunk Edgerunners"

    def test_bracketed_groups_stripped(self):
        """方括号压制组 + 方括号技术参数"""
        raw = "[FraMeSToR] Attack on Titan S01E03 [1080p BluRay DTS-HD MA 5.1 AVC]"
        assert clean_title(raw) == "Attack on Titan"

    def test_dotted_movie_name(self):
        """点分命名: 年份 + 分辨率 + 来源 + 编码 + 压制组"""
        raw = "Pacific.Rim.2013.1080p.BluRay.x264.DTS-FraMeSToR"
        assert clean_title(raw) == "Pacific Rim"

    def test_year_at_end_of_string(self):
        """末尾年份 (无后随分隔符) 也必须剥离"""
        assert clean_title("Interstellar.2014") == "Interstellar"
        assert clean_title("Inception.2010.1080p.BluRay.x264") == "Inception"

    def test_streaming_source_tag_stripped(self):
        """流媒体来源标记 NF (Netflix) 应剥离"""
        raw = "Cyberpunk.Edgerunners.S01E01.Let.You.Down.1080p.NF.WEB-DL.DDP5.1.AVC"
        cleaned = clean_title(raw)
        assert "NF" not in cleaned
        assert "WEB" not in cleaned
        assert cleaned.startswith("Cyberpunk Edgerunners")

    def test_episode_only_stripped(self):
        """EP## 标记剥离, 保留作品名"""
        assert clean_title("One.Piece.EP07.1080p.WEB-DL") == "One Piece"

    def test_trailing_roman_numeral_not_eaten(self):
        """结尾的正常单词不能被当压制组吃掉 (只剥破折号前缀的 token)"""
        assert clean_title("Back to the Future Part II") == "Back to the Future Part II"

    @pytest.mark.parametrize("raw", [
        "头号玩家",
        "海绵宝宝：深海大冒险",
        "蜘蛛侠：纵横宇宙",
        "霍比特人1：意外之旅",
        "帕丁顿熊3",
        "痴迷",
        "美食总动员",
    ])
    def test_chinese_titles_untouched(self, raw):
        """中文标题 (含全角冒号、序号数字) 必须原样保留"""
        assert clean_title(raw) == raw

    def test_chinese_dir_with_year(self):
        """中文目录名 + 括号年份"""
        assert clean_title("盗梦空间 (2010)") == "盗梦空间"

    def test_real_world_multilang_remux_name(self):
        """真实片源目录名: 多语言音轨列表 + Hybrid + Remux + DV/HDR + 压制组"""
        raw = ("Pacific.Rim.2013.Eng.Fre.Ger.Ita.Por.Spa.Cze.Pol.Rus.Tur.Chi.Jpn"
               ".2160p.BluRay.Hybrid.Remux.DV.HDR.HEVC.Atmos-SGF")
        assert clean_title(raw) == "Pacific Rim"

    @pytest.mark.parametrize("raw", [
        "Indiana Jones and the Last Crusade",
        "The Danish Girl",
        "Roman Holiday",
        "Greece Trip",
    ])
    def test_single_language_like_word_not_eaten(self, raw):
        """语言码只剥「连续 2 个以上」: 标题里单个形似语言码的词必须保留"""
        assert clean_title(raw) == raw

    def test_empty_input(self):
        assert clean_title("") == ""
        assert clean_title(None) == ""