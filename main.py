import asyncio
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Image, Video
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

try:
    from eporner_api import Client, Video, Pornstar
except ImportError:
    logger.error("Eporner-API 未安装，请运行: pip install --upgrade Eporner-API")
    Client = None
    Video = None
    Pornstar = None

# 硬编码正确的URL
ROOT_URL = "https://www.eporner.com"


@register("eporner", "EPorner", "EPorner视频信息查询插件", "1.0.0")
class EPornerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.client: Optional[Client] = None
        self.cache_dir = Path("data/plugins/eporner_cache")
        self.last_cache_files = []
        
        # 从配置获取设置
        config = self.context.get_config(umo="global")
        self.proxy = config.get("eporner_proxy", "")
        self.blur_level = config.get("eporner_blur_level", 5)  # 默认模糊程度为5
        
    async def initialize(self):
        """插件初始化"""
        if Client is None:
            logger.error("Eporner-API 未安装，插件无法正常工作")
            return
            
        # 创建缓存目录
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化客户端
        try:
            self.client = Client()
            if self.proxy:
                # 配置代理
                self.client.core.session.trust_env = True
                self.client.core.session.connector = aiohttp.TCPConnector(limit=10)
            logger.info("EPorner插件初始化成功")
        except Exception as e:
            logger.error(f"EPorner插件初始化失败: {e}")
    
    async def terminate(self):
        """插件销毁时清理资源"""
        await self._cleanup_cache()
    
    async def _cleanup_cache(self):
        """清理上一次的缓存文件"""
        for file_path in self.last_cache_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"已清理缓存文件: {file_path}")
            except Exception as e:
                logger.error(f"清理缓存文件失败 {file_path}: {e}")
        self.last_cache_files.clear()
    
    async def _download_image(self, url: str) -> Optional[str]:
        """下载图片到缓存目录"""
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                if self.proxy:
                    connector = aiohttp.TCPConnector()
                    session = aiohttp.ClientSession(connector=connector)
                
                async with session.get(url, proxy=self.proxy if self.proxy else None) as response:
                    if response.status == 200:
                        content = await response.read()
                        filename = f"temp_{asyncio.get_event_loop().time()}.jpg"
                        file_path = self.cache_dir / filename
                        with open(file_path, 'wb') as f:
                            f.write(content)
                        self.last_cache_files.append(str(file_path))
                        return str(file_path)
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
        return None
    
    def _blur_image(self, image_path: str, blur_level: int) -> str:
        """对图片进行模糊处理"""
        if blur_level <= 0:
            logger.info(f"模糊程度为{blur_level}，跳过模糊处理")
            return image_path
        
        try:
            from PIL import Image, ImageFilter
            img = Image.open(image_path)
            blurred = img.filter(ImageFilter.GaussianBlur(radius=blur_level))
            blurred_path = image_path.replace('.jpg', '_blurred.jpg')
            blurred.save(blurred_path)
            self.last_cache_files.append(blurred_path)
            logger.info(f"图片模糊处理完成，模糊程度: {blur_level}")
            return blurred_path
        except ImportError:
            logger.warning("PIL未安装，无法进行模糊处理")
            return image_path
        except Exception as e:
            logger.error(f"图片模糊处理失败: {e}")
            return image_path
    
    def _add_zero_width_space(self, text: str) -> str:
        """在文本末尾添加零宽空格防止被strip"""
        return text + "\u200E"
    
    @filter.command("ep_video")
    async def get_video_info(self, event: AstrMessageEvent, video_id: str = ""):
        """获取视频信息 - 用法: /ep_video <视频ID>"""
        if Client is None:
            yield event.plain_result(self._add_zero_width_space("Eporner-API 未安装，请联系管理员"))
            return
        
        if not video_id:
            yield event.plain_result(self._add_zero_width_space("请提供视频ID\n用法: /ep_video <视频ID>"))
            return
        
        try:
            # 清理上一次的缓存
            await self._cleanup_cache()
            
            # 判断输入的是ID还是完整URL
            if video_id.startswith("http"):
                # 完整URL，直接使用
                video_url = video_id
            else:
                # 只有ID，构建完整URL
                video_url = f"{ROOT_URL}/video-{video_id}/"
            
            logger.info(f"获取视频信息: {video_url}")
            
            # 获取视频信息
            video = self.client.get_video(video_url, enable_html_scraping=True)
            
            # 构建消息
            info_text = f"""📹 视频信息
━━━━━━━━━━━━━━━━
📌 标题: {video.title}
👁️ 观看: {video.views}
⭐ 评分: {video.rate}
📅 发布: {video.publish_date}
⏱️ 时长: {video.length_minutes}分钟
🏷️ 标签: {', '.join(video.tags[:5])}"""
            
            # 获取缩略图
            thumbnail_url = video.thumbnail
            if thumbnail_url:
                image_path = await self._download_image(thumbnail_url)
                if image_path:
                    # 应用模糊处理
                    blurred_path = self._blur_image(image_path, self.blur_level)
                    
                    # 发送图片和文本
                    yield event.chain_result([
                        Image.fromFileSystem(blurred_path),
                        Plain(self._add_zero_width_space(info_text))
                    ])
                else:
                    yield event.plain_result(self._add_zero_width_space(info_text))
            else:
                yield event.plain_result(self._add_zero_width_space(info_text))
                
        except Exception as e:
            logger.error(f"获取视频信息失败: {e}")
            yield event.plain_result(self._add_zero_width_space(f"获取视频信息失败: {str(e)}"))
    
    @filter.command("ep_search")
    async def search_videos(self, event: AstrMessageEvent, query: str = ""):
        """搜索视频 - 用法: /ep_search <关键词>"""
        if Client is None:
            yield event.plain_result(self._add_zero_width_space("Eporner-API 未安装，请联系管理员"))
            return
        
        if not query:
            yield event.plain_result(self._add_zero_width_space("请提供搜索关键词\n用法: /ep_search <关键词>"))
            return
        
        try:
            await self._cleanup_cache()
            
            # 搜索视频
            results = list(self.client.search_videos(
                query=query,
                sorting_gay="no",
                sorting_order="newest",
                sorting_low_quality="no",
                page=1,
                per_page=5,
                enable_html_scraping=False
            ))
            
            if not results:
                yield event.plain_result(self._add_zero_width_space("未找到相关视频"))
                return
            
            # 构建结果消息
            result_text = f"🔍 搜索结果: {query}\n━━━━━━━━━━━━━━━━\n"
            
            for i, video in enumerate(results[:5], 1):
                video_id = video.video_id
                result_text += f"{i}. {video.title}\n"
                result_text += f"   ID: {video_id}\n"
                result_text += f"   时长: {video.length_minutes}分钟 | 观看: {video.views}\n"
                result_text += f"   查看详情: /ep_video {video_id}\n\n"
            
            yield event.plain_result(self._add_zero_width_space(result_text))
            
        except Exception as e:
            logger.error(f"搜索视频失败: {e}")
            yield event.plain_result(self._add_zero_width_space(f"搜索视频失败: {str(e)}"))
    
    @filter.command("ep_pornstar")
    async def get_pornstar_info(self, event: AstrMessageEvent, pornstar_id: str = ""):
        """获取演员信息 - 用法: /ep_pornstar <演员ID>"""
        if Client is None:
            yield event.plain_result(self._add_zero_width_space("Eporner-API 未安装，请联系管理员"))
            return
        
        if not pornstar_id:
            yield event.plain_result(self._add_zero_width_space("请提供演员ID\n用法: /ep_pornstar <演员ID>"))
            return
        
        try:
            await self._cleanup_cache()
            
            # 构建完整URL
            pornstar_url = f"{ROOT_URL}/pornstar/{pornstar_id}"
            
            # 获取演员信息
            pornstar = self.client.get_pornstar(pornstar_url, enable_html_scraping=True)
            
            # 构建消息
            info_text = f"""👤 演员信息
━━━━━━━━━━━━━━━━
📌 姓名: {pornstar.name}
👥 订阅者: {pornstar.subscribers}
📊 排名: {pornstar.pornstar_rank}
👁️ 个人主页浏览: {pornstar.profile_views}
🎬 视频数: {pornstar.video_amount}
📷 照片数: {pornstar.photos_amount}
🎥 视频观看: {pornstar.video_views}
📸 照片观看: {pornstar.photo_views}
🌍 国家: {pornstar.country}
🎂 年龄: {pornstar.age}
👁️ 眼睛颜色: {pornstar.eye_color}
💇 发色: {pornstar.hair_color}
📏 身高: {pornstar.height}
⚖️ 体重: {pornstar.weight}
📏 三围: {pornstar.measurements}
🍷 罩杯: {pornstar.cup}
🎭 种族: {pornstar.ethnicity}"""
            
            # 获取头像
            picture_url = pornstar.picture
            if picture_url:
                image_path = await self._download_image(picture_url)
                if image_path:
                    blurred_path = self._blur_image(image_path, self.blur_level)
                    yield event.chain_result([
                        Image.fromFileSystem(blurred_path),
                        Plain(self._add_zero_width_space(info_text))
                    ])
                else:
                    yield event.plain_result(self._add_zero_width_space(info_text))
            else:
                yield event.plain_result(self._add_zero_width_space(info_text))
                
        except Exception as e:
            logger.error(f"获取演员信息失败: {e}")
            yield event.plain_result(self._add_zero_width_space(f"获取演员信息失败: {str(e)}"))
    
    @filter.command("ep_category")
    async def get_category_videos(self, event: AstrMessageEvent, category: str = ""):
        """获取分类视频 - 用法: /ep_category <分类名>"""
        if Client is None:
            yield event.plain_result(self._add_zero_width_space("Eporner-API 未安装，请联系管理员"))
            return
        
        if not category:
            yield event.plain_result(self._add_zero_width_space("请提供分类名\n用法: /ep_category <分类名>"))
            return
        
        try:
            await self._cleanup_cache()
            
            # 获取分类视频
            results = list(self.client.get_videos_by_category(
                category=category,
                enable_html_scraping=False,
                videos_concurrency=3,
                pages_concurrency=1
            ))
            
            if not results:
                yield event.plain_result(self._add_zero_width_space(f"未找到分类 '{category}' 的视频"))
                return
            
            # 构建结果消息
            result_text = f"📂 分类: {category}\n━━━━━━━━━━━━━━━━\n"
            
            for i, video in enumerate(results[:5], 1):
                video_id = video.video_id
                result_text += f"{i}. {video.title}\n"
                result_text += f"   ID: {video_id}\n"
                result_text += f"   时长: {video.length_minutes}分钟 | 观看: {video.views}\n"
                result_text += f"   查看详情: /ep_video {video_id}\n\n"
            
            yield event.plain_result(self._add_zero_width_space(result_text))
            
        except Exception as e:
            logger.error(f"获取分类视频失败: {e}")
            yield event.plain_result(self._add_zero_width_space(f"获取分类视频失败: {str(e)}"))
