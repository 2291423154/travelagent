"""Amadeus 酒店 API 客户端 — OAuth2 认证 + 酒店搜索"""
import os
import logging
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BASE_URL = "https://test.api.amadeus.com"
TOKEN_URL = f"{BASE_URL}/v1/security/oauth2/token"

# 从 .env 加载
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
if os.path.exists(_ENV_PATH):
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except ImportError:
        pass


class AmadeusClient:
    """Amadeus Self-Service API — OAuth2 + 酒店搜索"""

    def __init__(self):
        self.client_id = os.getenv("AMADEUS_API_KEY", "")
        self.client_secret = os.getenv("AMADEUS_API_SECRET", "")
        self._token = None
        self._token_expiry = None

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _authenticate(self):
        """OAuth2 Client Credentials -- 获取 access token，有效 30 分钟"""
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = datetime.now() + timedelta(seconds=data["expires_in"] - 60)
        logger.info("Amadeus OAuth2 认证成功")

    def _get(self, path: str, params: dict = None) -> dict:
        self._authenticate()
        resp = requests.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def search_hotels(
        self,
        city_code: str = None,
        latitude: float = None,
        longitude: float = None,
        check_in: str = None,
        check_out: str = None,
        adults: int = 1,
        radius: int = 20,
    ) -> list[dict]:
        """搜索酒店并返回结构化结果。

        支持按城市代码（如 PEK=北京, SHA=上海, CTU=成都, HGH=杭州, SIA=西安）
        或经纬度搜索。

        Returns:
            [{"name": "北京饭店", "rating": "4.5", "price": "850 CNY", ...}, ...]
        """
        if not self.enabled:
            return [{"error": "Amadeus API 未配置，请在 .env 中设置 AMADEUS_API_KEY 和 AMADEUS_API_SECRET"}]

        params = {"adults": adults, "radius": radius, "radiusUnit": "KM"}
        if check_in:
            params["checkInDate"] = check_in
        if check_out:
            params["checkOutDate"] = check_out
        if city_code:
            params["cityCode"] = city_code
        elif latitude and longitude:
            params["latitude"] = latitude
            params["longitude"] = longitude

        try:
            data = self._get("/v3/shopping/hotel-offers", params)
        except Exception as e:
            logger.warning(f"Amadeus 酒店搜索失败: {e}")
            return [{"error": f"酒店搜索失败: {str(e)}"}]

        results = []
        for hotel in data.get("data", [])[:5]:  # Top 5
            h = hotel.get("hotel", {})
            offers = hotel.get("offers", [])
            price_info = offers[0].get("price", {}) if offers else {}
            results.append({
                "name": h.get("name", "未知"),
                "city": h.get("cityCode", ""),
                "rating": h.get("rating", "N/A"),
                "price": f"{price_info.get('total', 'N/A')} {price_info.get('currency', '')}" if price_info else "暂无报价",
                "available": len(offers) > 0,
            })
        return results

    def format_for_llm(self, results: list[dict]) -> str:
        """搜索结果格式化为 LLM 可读文本"""
        if not results:
            return "未找到符合条件的酒店。"
        if "error" in results[0]:
            return results[0]["error"]

        lines = ["【Amadeus 酒店搜索结果】"]
        for i, r in enumerate(results, 1):
            avail = "可预订" if r["available"] else "暂无空房"
            lines.append(f"{i}. {r['name']} | 评分: {r['rating']} | {r['price']} | {avail}")
        return "\n".join(lines)


# 城市名 → IATA city code 映射（常用旅游城市）
CITY_CODES = {
    "北京": "PEK", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
    "杭州": "HGH", "成都": "CTU", "重庆": "CKG", "西安": "SIA",
    "南京": "NKG", "苏州": "SHA",  # 苏州无机场，用上海
    "昆明": "KMG", "三亚": "SYX", "厦门": "XMN", "桂林": "KWL",
    "丽江": "LJG", "哈尔滨": "HRB", "青岛": "TAO", "大连": "DLC",
}

# 全局单例
_amadeus_client: AmadeusClient = None


def get_amadeus() -> AmadeusClient:
    global _amadeus_client
    if _amadeus_client is None:
        _amadeus_client = AmadeusClient()
    return _amadeus_client
