# -*- coding: utf-8 -*-
"""
处理插件的所有用户命令
"""
import asyncio
import os
import random
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any

from astrbot.api.event import AstrMessageEvent

from .card_generator import generate_card_image
from .constants import HELP_MSG
from .utils import get_beijing_time

if TYPE_CHECKING:
    from .main import VocabCardPlugin


async def generate_and_send_card(word: Dict[str, Any], plugin_dir: Path, event: AstrMessageEvent):
    """
    生成单词卡片并发送，然后清理临时文件
    
    Args:
        word: 单词数据字典，包含 word, phonetic, pos, definition_cn, example 等字段
        plugin_dir: 插件目录路径
        event: 消息事件
        
    Yields:
        event results
    """
    image_path = generate_card_image(word, plugin_dir)
    yield event.image_result(image_path)
    
    # 清理临时文件
    if os.path.exists(image_path):
        os.remove(image_path)


async def handle_vocab(plugin: "VocabCardPlugin", event: AstrMessageEvent):
    """处理 /vocab 命令"""
    user_id = event.get_user_id()
    mode = plugin.config.get("learning_mode", "random")
    word = plugin.progress_manager.select_word(user_id=user_id, mode=mode)

    if not word:
        yield event.plain_result("没有可用的单词数据")
        return

    try:
        async for result in generate_and_send_card(word, plugin.plugin_dir, event):
            yield result
        
        plugin.progress_manager.mark_word_sent(word["word"], user_id=user_id)
            
    except Exception as e:
        plugin.logger.error(f"生成卡片失败: {e}")
        yield event.plain_result(f"❌ 生成卡片失败: {e}")


async def handle_status(plugin: "VocabCardPlugin", event: AstrMessageEvent):
    """处理 /vocab_status 命令，只显示个人进度"""
    user_id = event.get_user_id()
    
    user_status = plugin.progress_manager.get_status(user_id)
    user_sent = user_status["sent"]
    user_total = user_status["total"]
    user_percent = user_sent * 100 // user_total if user_total > 0 else 0
    
    msg = f"""📊 个人单词学习进度
━━━━━━━━━━━━━━━━
   - 已学习: {user_sent} / {user_total} 个
   - 完成度: {user_percent}%
   - 上次学习: {user_status["last_date"]}
━━━━━━━━━━━━━━━━"""
    yield event.plain_result(msg)


async def handle_register(plugin: "VocabCardPlugin", event: AstrMessageEvent):
    """处理 /vocab_register 命令"""
    umo = event.unified_msg_origin
    target_groups = plugin.config.get("target_groups", [])

    if umo in target_groups:
        yield event.plain_result("当前会话已注册过了 ✅")
        return

    target_groups.append(umo)
    plugin.config["target_groups"] = target_groups
    plugin.config.save_config()

    push_time = plugin.config.get("push_time_send", "08:00")
    yield event.plain_result(f"注册成功！🎉\n将在每天 {push_time} 推送单词卡片")


async def handle_unregister(plugin: "VocabCardPlugin", event: AstrMessageEvent):
    """处理 /vocab_unregister 命令"""
    umo = event.unified_msg_origin
    target_groups = plugin.config.get("target_groups", [])

    if umo not in target_groups:
        yield event.plain_result("当前会话未注册 ❌")
        return

    target_groups.remove(umo)
    plugin.config["target_groups"] = target_groups
    plugin.config.save_config()

    yield event.plain_result("已取消注册 👋")


async def handle_test_push(plugin: "VocabCardPlugin", event: AstrMessageEvent, delay_seconds: str):
    """处理 /vocab_test 命令"""
    delay = int(delay_seconds) if delay_seconds.isdigit() else 0

    if delay == 0:
        # 快速测试
        try:
            user_id = event.get_user_id()
            word = plugin.progress_manager.select_word(user_id=user_id)
            if not word:
                yield event.plain_result("没有可用的单词")
                return

            image_path = generate_card_image(word, plugin.plugin_dir)
            yield event.plain_result(f"📚 测试单词: {word['word']}")
            yield event.image_result(image_path)
            
            plugin.progress_manager.mark_word_sent(word['word'], user_id=user_id)

            if os.path.exists(image_path):
                os.remove(image_path)
        except Exception as e:
            plugin.logger.error(f"测试推送失败: {e}")
            yield event.plain_result(f"❌ 测试失败: {e}")
    else:
        # 完整流程测试
        original_targets = plugin.config.get("target_groups", []).copy()
        umo = event.unified_msg_origin
        temp_registered = False
        try:
            if umo not in original_targets:
                plugin.config["target_groups"].append(umo)
                temp_registered = True
                yield event.plain_result("✅ 临时注册当前会话")
            else:
                yield event.plain_result("ℹ️ 当前会话已注册")

            now = get_beijing_time()
            target_time = now + asyncio.timedelta(seconds=delay)
            yield event.plain_result(f"⏰ 将在 {delay} 秒后执行推送 (目标: {target_time.strftime('%H:%M:%S')})")
            
            await asyncio.sleep(delay)
            yield event.plain_result("⏱️ 时间到！开始执行...")

            yield event.plain_result("🎨 步骤 1/2: 生成单词卡片...")
            await plugin._generate_daily_card()
            if plugin._cached_image_path:
                word_text = plugin._current_word.get('word', '?')
                yield event.plain_result(f"✅ 卡片生成成功: {word_text}")
            else:
                yield event.plain_result("❌ 卡片生成失败")
                return

            yield event.plain_result("📤 步骤 2/2: 推送到已注册群...")
            await plugin._push_daily_card()
            yield event.plain_result("✅ 推送完成")
        except Exception as e:
            error_detail = traceback.format_exc()
            plugin.logger.error(f"测试推送失败:\n{error_detail}")
            yield event.plain_result(f"❌ 测试失败: {e}")
        finally:
            if temp_registered:
                plugin.config["target_groups"] = original_targets
                plugin.config.save_config()
                yield event.plain_result("🔄 已恢复原始注册列表")


async def handle_preview(plugin: "VocabCardPlugin", event: AstrMessageEvent, word_input: str):
    """处理 /vocab_preview 命令"""
    if word_input:
        word = next((w for w in plugin.words if w["word"].lower() == word_input.lower()), None)
        if not word:
            yield event.plain_result(f"未找到单词: {word_input}")
            return
    else:
        word = random.choice(plugin.words) if plugin.words else None
        if not word:
            yield event.plain_result("没有可用的单词数据")
            return

    info_msg = f"""🔍 单词预览
━━━━━━━━━━━━━━━━━━━━
📝 单词: {word.get('word', '')}
🔊 音标: {word.get('phonetic', '')}
📚 词性: {word.get('pos', '')}
📖 释义: {word.get('definition_cn', '')}
💬 例句: {word.get('example', '')[:50]}...
━━━━━━━━━━━━━━━━━━━━
⏳ 正在生成卡片图片..."""
    yield event.plain_result(info_msg)

    try:
        image_path = generate_card_image(word, plugin.plugin_dir)
        yield event.plain_result("✅ 图片生成成功！")
        yield event.image_result(image_path)
        if os.path.exists(image_path):
            os.remove(image_path)
    except Exception as e:
        error_detail = traceback.format_exc()
        plugin.logger.error(f"预览失败: {error_detail}")
        yield event.plain_result(f"❌ 生成失败: {e}")


async def handle_push_now(plugin: "VocabCardPlugin", event: AstrMessageEvent):
    """处理 /vocab_now 命令"""
    yield event.plain_result("🚀 开始执行完整推送流程...")

    target_groups = plugin.config.get("target_groups", [])
    if not target_groups:
        yield event.plain_result("⚠️ 没有已注册的推送目标，请先使用 /vocab_register 注册")
        return

    yield event.plain_result(f"📋 已注册 {len(target_groups)} 个推送目标")

    try:
        yield event.plain_result("⏳ 步骤1: 生成单词卡片...")
        await plugin._generate_daily_card()
        if not plugin._cached_image_path:
            yield event.plain_result("❌ 卡片生成失败")
            return
        yield event.plain_result(f"✅ 卡片已生成: {plugin._current_word.get('word', '?')}")

        yield event.plain_result("⏳ 步骤2: 推送到所有已注册群聊...")
        await plugin._push_daily_card()
        yield event.plain_result("✅ 推送完成！")
    except Exception as e:
        plugin.logger.error(f"立即推送失败: {traceback.format_exc()}")
        yield event.plain_result(f"❌ 推送失败: {e}")


async def handle_vocab_recap(plugin: "VocabCardPlugin", event: AstrMessageEvent, count: str = "1"):
    """处理 /vocab_recap 命令"""
    user_id = event.get_user_id()
    
    # 解析复习数量参数
    try:
        recap_count = int(count)
        if recap_count <= 0:
            yield event.plain_result("❌ 复习数量必须大于0")
            return
        if recap_count > 10:
            yield event.plain_result("❌ 一次最多只能复习10个单词")
            return
    except ValueError:
        yield event.plain_result("❌ 请输入有效的数字")
        return
    
    # 获取用户已学习的单词总数
    user_status = plugin.progress_manager.get_status(user_id)
    learned_count = user_status["sent"]
    
    if learned_count == 0:
        yield event.plain_result("📚 你还没有学习过任何单词，请先使用 /vocab 学习新单词")
        return
    
    # 检查复习数量是否超过已学习的总数
    if recap_count > learned_count:
        yield event.plain_result(f"⚠️ 你只学习了 {learned_count} 个单词，将为你复习全部已学单词")
        recap_count = learned_count
    
    # 选择要复习的单词
    review_words = plugin.progress_manager.select_review_words(user_id=user_id, count=recap_count)
    
    if not review_words:
        yield event.plain_result("❌ 没有可复习的单词")
        return
    
    yield event.plain_result(f"📖 开始复习 {len(review_words)} 个单词...")
    
    # 为每个单词生成卡片
    for idx, word in enumerate(review_words, 1):
        try:
            yield event.plain_result(f"[{idx}/{len(review_words)}] {word['word']}")
            async for result in generate_and_send_card(word, plugin.plugin_dir, event):
                yield result
        except Exception as e:
            plugin.logger.error(f"生成复习卡片失败: {e}")
            yield event.plain_result(f"❌ 生成卡片失败: {word['word']}")
    
    yield event.plain_result(f"✅ 复习完成！共复习了 {len(review_words)} 个单词")


async def handle_help(plugin: "VocabCardPlugin", event: AstrMessageEvent):
    """处理 /vocab_help 命令"""
    yield event.plain_result(HELP_MSG)
