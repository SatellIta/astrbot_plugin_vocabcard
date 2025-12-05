# -*- coding: utf-8 -*-
"""
管理用户和全局学习进度
"""
import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Set

from astrbot.api import logger

from .utils import get_beijing_time


class ProgressManager:
    def __init__(self, data_dir: Path, words: List[Dict]):
        self.progress_file = data_dir / "progress.json"
        self.words = words
        self.word_set = {w['word'] for w in self.words}
        self.word_map = {w['word']: w for w in self.words}
        self.progress = self._load_progress()

    def _load_progress(self) -> Dict:
        """
        加载学习进度。
        """
        if not self.progress_file.exists():
            return self._get_default_progress()

        try:
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 检查并补全可能缺失的键
            if "global" not in data:
                data["global"] = {"sent_words": [], "last_push_date": ""}
            if "users" not in data:
                data["users"] = {}

            return data

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载进度数据失败: {e}，将使用默认进度。")
            return self._get_default_progress()

    def _save_progress(self, progress_data: Optional[Dict] = None):
        """保存学习进度"""
        data_to_save = progress_data or self.progress
        try:
            # 在保存前对每个用户的已学单词列表进行排序
            if "users" in data_to_save:
                for user_id, user_data in data_to_save["users"].items():
                    if "sent_words" in user_data:
                        user_data["sent_words"].sort()

            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"保存进度数据失败: {e}")

    def _get_default_progress(self) -> Dict:
        """返回一个空的默认进度结构"""
        return {
            "global": {
                "sent_words": [],
                "last_push_date": ""
            },
            "users": {}
        }

    def _get_user_progress(self, user_id: str) -> Dict:
        """获取指定用户的进度，如果不存在则创建"""
        if user_id not in self.progress["users"]:
            self.progress["users"][user_id] = {
                "sent_words": [],
                "last_seen_date": ""
            }
        return self.progress["users"][user_id]

    def select_word(self, user_id: Optional[str] = None, mode: str = "random") -> Optional[Dict]:
        """
        选择一个单词。
        如果提供了 user_id，则基于用户进度选择。
        否则，基于全局进度选择。
        """
        sent_words: Set[str]
        if user_id:
            user_progress = self._get_user_progress(user_id)
            sent_words = set(user_progress.get("sent_words", []))
        else: # 全局
            sent_words = set(self.progress["global"].get("sent_words", []))

        available_words = [w for w in self.words if w["word"] not in sent_words]

        if not available_words:
            # 如果没有可用单词，重置进度并重新选择
            logger.info(f"用户 {user_id or 'global'} 的单词已全部学习，重置进度。")
            if user_id:
                self.progress["users"][user_id]["sent_words"] = []
            else:
                self.progress["global"]["sent_words"] = []
            self._save_progress()
            available_words = self.words

        if not available_words:
            return None

        if mode == "sequential":
            return available_words[0]
        
        return random.choice(available_words)

    def mark_word_sent(self, word: str, user_id: Optional[str] = None):
        """
        标记单词已发送。
        如果提供了 user_id，则标记给用户。
        否则，标记为全局。
        """
        if word not in self.word_set:
            return

        now_str = get_beijing_time().strftime("%Y-%m-%d")

        if user_id:
            user_progress = self._get_user_progress(user_id)
            if word not in user_progress["sent_words"]:
                user_progress["sent_words"].append(word)
            user_progress["last_seen_date"] = now_str
        else: # 全局
            if word not in self.progress["global"]["sent_words"]:
                self.progress["global"]["sent_words"].append(word)
            self.progress["global"]["last_push_date"] = now_str
        
        self._save_progress()

    def get_status(self, user_id: Optional[str] = None) -> Dict:
        """获取用户或全局的学习状态"""
        total = len(self.words)
        if user_id:
            user_progress = self._get_user_progress(user_id)
            sent = len(user_progress.get("sent_words", []))
            last_date = user_progress.get("last_seen_date", "从未")
            return {"type": "user", "sent": sent, "total": total, "last_date": last_date}
        else:
            sent = len(self.progress["global"].get("sent_words", []))
            last_date = self.progress["global"].get("last_push_date", "从未")
            return {"type": "global", "sent": sent, "total": total, "last_date": last_date}

    def select_recap_words(self, user_id: str, count: int) -> List[Dict]:
        """
        从用户已学单词中随机选择指定数量的单词进行复习。
        """
        user_progress = self._get_user_progress(user_id)
        sent_words = user_progress.get("sent_words", [])

        if not sent_words:
            return []

        # 如果请求的数量大于已学数量，则调整为已学数量
        if count > len(sent_words):
            count = len(sent_words)

        # 随机选择单词
        recap_word_strings = random.sample(sent_words, count)

        # 获取完整的单词信息
        recap_words = [self.word_map[word] for word in recap_word_strings if word in self.word_map]

        return recap_words
