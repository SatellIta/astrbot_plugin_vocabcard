# -*- coding: utf-8 -*-
"""
AstrBot 每日单词卡片插件
每日定时生成单词卡片并推送到群聊，支持用户独立进度。
"""
import asyncio
import datetime
import json
import os
import traceback
from pathlib import Path
from typing import Optional, Dict, List

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register, StarTools

from . import actions
from .card_generator import generate_card_image
from .progress_manager import ProgressManager
from .utils import get_beijing_time

@register(
    "vocabcard", 
    "SatellIta", 
    "每日英语单词卡片（Pillow版）", 
    "2.0.0",
    "https://github.com/SatellIta/astrbot_plugin_vocabcard")
class VocabCardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).parent

        # 定义新旧数据目录
        self.legacy_data_dir = self.plugin_dir / "data"
        self.data_dir = StarTools.get_data_dir() / "vocabcard"

        # 先声明，在 initialize 中进行初始化
        self.words: List[Dict] = []
        self.progress_manager: Optional[ProgressManager] = None

        # 定时任务相关
        self._scheduler_task: Optional[asyncio.Task] = None
        self._cached_image_path: Optional[str] = None
        self._current_word: Optional[Dict] = None
        self._today_generated: bool = False
        self._last_check_date: str = ""

    def _load_words(self) -> List[Dict]:
        """从标准数据目录加载词汇数据"""
        words_file = self.data_dir / "words.json"
        if not words_file.exists():
            logger.warning(f"词汇文件不存在: {words_file}")
            return []
        try:
            with open(words_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载词汇数据失败: {e}")
            return []

    async def initialize(self):
        """异步初始化, 包括数据迁移"""
        # 1. 确保目标数据目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 2. 执行一次性数据迁移
        await self._migrate_data()

        # 3. 从新目录加载数据并初始化管理器
        self.words = self._load_words()
        self.progress_manager = ProgressManager(self.data_dir, self.words)
        
        logger.info(f"单词卡片插件 v2.1 初始化完成，已加载 {len(self.words)} 个单词。数据目录: {self.data_dir}")

    async def _migrate_data(self):
        """将旧数据目录的文件迁移到新目录"""
        if not self.legacy_data_dir.is_dir():
            return # 旧目录不存在，无需迁移

        logger.info("检测到旧版 data 目录，开始进行数据迁移...")
        files_to_migrate = ["words.json", "progress.json"]
        migrated_count = 0

        for filename in files_to_migrate:
            old_file = self.legacy_data_dir / filename
            new_file = self.data_dir / filename
            
            if old_file.exists() and not new_file.exists():
                try:
                    # 使用 pathlib.rename 进行移动，无需新库
                    old_file.rename(new_file)
                    logger.info(f"  - 已将 {filename} 迁移到新目录。")
                    migrated_count += 1
                except Exception as e:
                    logger.error(f"  - 迁移 {filename} 失败: {e}")
        
        if migrated_count > 0:
            logger.info("数据迁移成功！")
        else:
            logger.info("无需迁移文件，或文件已存在于新目录。")

        # 尝试删除旧的空目录
        try:
            if not any(self.legacy_data_dir.iterdir()):
                self.legacy_data_dir.rmdir()
                logger.info("已移除空的旧版 data 目录。")
        except Exception as e:
            logger.warning(f"移除旧版 data 目录失败（可能非空）: {e}")

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        """AstrBot 启动后启动定时任务"""
        if not self.words:
            logger.error("词汇库为空，定时推送功能无法启动。")
            return
        self._scheduler_task = asyncio.create_task(self._schedule_loop())
        logger.info("单词卡片定时任务已启动")

    async def _schedule_loop(self):
        """定时任务主循环 - 智能睡眠，精准触发"""
        while True:
            try:
                # 确保 progress_manager 已初始化
                if not self.progress_manager:
                    logger.warning("ProgressManager尚未初始化，等待10秒...")
                    await asyncio.sleep(10)
                    continue

                now = get_beijing_time()
                today_str = now.strftime("%Y-%m-%d")

                # 解析配置的时间
                gen_time = self._parse_time(self.config.get("push_time_generate", "07:30"))
                push_time = self._parse_time(self.config.get("push_time_send", "08:00"))

                # 每天0点重置标记
                if self._last_check_date != today_str:
                    self._today_generated = False
                    self._last_check_date = today_str

                # 计算下一个目标时间
                next_target = self._calculate_next_target_time(now, gen_time, push_time)

                if next_target:
                    sleep_seconds = (next_target - now).total_seconds()

                    # 如果距离目标时间超过 60 秒，先睡到提前 30 秒
                    if sleep_seconds > 60:
                        sleep_until = sleep_seconds - 30
                        logger.debug(f"距离下次任务还有 {sleep_seconds:.0f} 秒，先睡眠 {sleep_until:.0f} 秒")
                        await asyncio.sleep(sleep_until)
                        continue

                    # 距离目标时间很近了，精确等待
                    if sleep_seconds > 0:
                        logger.debug(f"即将执行任务，精确等待 {sleep_seconds:.1f} 秒")
                        await asyncio.sleep(sleep_seconds)

                # 重新获取当前时间（睡眠后）
                now = get_beijing_time()

                # 执行生成任务
                if now.hour == gen_time[0] and now.minute == gen_time[1]:
                    if not self._today_generated:
                        logger.info("开始生成每日单词卡片...")
                        await self._generate_daily_card()
                        self._today_generated = True

                # 执行推送任务
                if now.hour == push_time[0] and now.minute == push_time[1]:
                    if self._cached_image_path and os.path.exists(self._cached_image_path):
                        logger.info("开始推送每日单词卡片...")
                        await self._push_daily_card()

                # 执行完任务后等待 10 秒，避免重复触发
                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"定时任务出错: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(60)  # 出错后等待 60 秒重试

    def _parse_time(self, time_str: str) -> tuple:
        """解析时间字符串 HH:MM"""
        try:
            parts = time_str.split(':')
            return (int(parts[0]), int(parts[1]))
        except:
            return (8, 0)  # 默认 8:00

    def _calculate_next_target_time(self, now: datetime.datetime, gen_time: tuple, push_time: tuple) -> Optional[datetime.datetime]:
        """计算下一个目标时间点（生成时间或推送时间中最近的一个）"""
        today = now.date()

        # 构建今天的生成时间和推送时间
        gen_datetime = datetime.datetime.combine(today, datetime.time(gen_time[0], gen_time[1]))
        push_datetime = datetime.datetime.combine(today, datetime.time(push_time[0], push_time[1]))

        # 找出所有未来的目标时间
        targets = []

        # 如果还没生成过，且生成时间未到
        if not self._today_generated and gen_datetime > now:
            targets.append(gen_datetime)

        # 如果推送时间未到
        if push_datetime > now:
            targets.append(push_datetime)

        # 如果今天的任务都完成了，计算明天的第一个任务（生成时间）
        if not targets:
            tomorrow = today + datetime.timedelta(days=1)
            next_gen = datetime.datetime.combine(tomorrow, datetime.time(gen_time[0], gen_time[1]))
            targets.append(next_gen)

        # 返回最近的目标时间
        return min(targets) if targets else None

    async def _generate_daily_card(self):
        """生成每日单词卡片（用于全局推送）"""
        mode = self.config.get("learning_mode", "random")
        word = self.progress_manager.select_word(user_id=None, mode=mode)
        if not word:
            logger.warning("没有可用的单词用于全局推送")
            return

        try:
            image_path = generate_card_image(word, self.plugin_dir)
            self._cached_image_path = image_path
            self._current_word = word
            self.progress_manager.mark_word_sent(word["word"], user_id=None)
            logger.info(f"已生成每日单词卡片: {word['word']}")
        except Exception as e:
            logger.error(f"生成每日卡片失败: {e}\n{traceback.format_exc()}")

    async def _push_daily_card(self):
        """推送卡片到已注册的群聊"""
        if not self._cached_image_path or not os.path.exists(self._cached_image_path):
            logger.warning("没有已生成的卡片可推送")
            return

        target_groups = self.config.get("target_groups", [])
        if not target_groups:
            logger.warning("没有已注册的推送目标")
            return

        success_count = 0
        word_text = self._current_word.get("word", "单词") if self._current_word else "单词"

        for umo in target_groups:
            try:
                # 构建消息链
                chain = MessageChain()
                chain.message(f"📚 每日单词: {word_text}")
                chain.file_image(self._cached_image_path)

                await self.context.send_message(umo, chain)
                success_count += 1
                logger.info(f"已推送到: {umo}")
            except Exception as e:
                logger.error(f"推送到 {umo} 失败: {e}")

        logger.info(f"每日单词推送完成: {success_count}/{len(target_groups)}")

        # 清理缓存的图片
        try:
            if os.path.exists(self._cached_image_path):
                os.remove(self._cached_image_path)
        except:
            pass
        self._cached_image_path = None

    @filter.command("vocab")
    async def cmd_vocab(self, event: AstrMessageEvent, param: str = None):
        """手动获取一个或多个单词卡片（记录个人进度）"""
        if not param:
            count = "1"
        else:
            count = param

        async for result in actions.handle_vocab(self, event, count):
            yield result

    @filter.command("vocab_status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看个人和全局的学习进度"""
        async for result in actions.handle_status(self, event):
            yield result

    @filter.command("vocab_register")
    async def cmd_register(self, event: AstrMessageEvent):
        """在当前会话注册接收每日单词推送"""
        async for result in actions.handle_register(self, event):
            yield result

    @filter.command("vocab_unregister")
    async def cmd_unregister(self, event: AstrMessageEvent):
        """取消当前会话的每日单词推送"""
        async for result in actions.handle_unregister(self, event):
            yield result

    @filter.command("vocab_test")
    async def cmd_test_push(self, event: AstrMessageEvent, delay_seconds: str = "0"):
        """测试推送功能"""
        async for result in actions.handle_test_push(self, event, delay_seconds):
            yield result

    @filter.command("vocab_preview")
    async def cmd_preview(self, event: AstrMessageEvent, word_input: str = ""):
        """预览单词卡片效果（调试用）"""
        async for result in actions.handle_preview(self, event, word_input):
            yield result

    @filter.command("vocab_now")
    async def cmd_push_now(self, event: AstrMessageEvent):
        """立即执行一次完整的生成+推送流程（模拟定时任务）"""
        async for result in actions.handle_push_now(self, event):
            yield result

    @filter.command("vocab_help")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        async for result in actions.handle_help(self, event):
            yield result

    @filter.command("vocab_recap")
    async def cmd_recap(self, event: AstrMessageEvent, param: str = None):
        """复习已学过的单词"""
        count = param if param else "1"
        async for result in actions.handle_recap(self, event, count):
            yield result

    async def terminate(self):
        """插件卸载时取消定时任务"""
        if self._scheduler_task:
            self._scheduler_task.cancel()
        logger.info("单词卡片插件已卸载")
