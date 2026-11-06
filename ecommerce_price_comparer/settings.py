# Scrapy settings for ecommerce_price_comparer project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html
import random

from rotating_proxies.middlewares import RotatingProxyMiddleware

BOT_NAME = "ecommerce_price_comparer"

SPIDER_MODULES = ["ecommerce_price_comparer.spiders"]
NEWSPIDER_MODULE = "ecommerce_price_comparer.spiders"

FEED_EXPORT_ENCODING = 'utf-8'
# Crawl responsibly by identifying yourself (and your website) on the user-agent

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

# Configure maximum concurrent requests performed by Scrapy (default: 16)
#CONCURRENT_REQUESTS = 32

# Configure a delay for requests for the same website (default: 0)
# See https://docs.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs

DOWNLOAD_DELAY = 1
RANDOMIZE_DOWNLOAD_DELAY = True
# The download delay setting will honor only one of:
#CONCURRENT_REQUESTS_PER_DOMAIN = 16
#CONCURRENT_REQUESTS_PER_IP = 16

# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
TELNETCONSOLE_ENABLED = False
# Override the default request headers:
#DEFAULT_REQUEST_HEADERS = {
#    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
#    "Accept-Language": "en",
#}

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
#SPIDER_MIDDLEWARES = {
#    "ecommerce_price_comparer.middlewares.EcommercePriceComparerSpiderMiddleware": 543,
#}

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
DOWNLOADER_MIDDLEWARES = {
   "ecommerce_price_comparer.middlewares.BlockInternalRequestsMiddleware": 100,
   # "ecommerce_price_comparer.middlewares.EcommercePriceComparerDownloaderMiddleware": 543,
   "ecommerce_price_comparer.middlewares.FakeUserAgentMiddleware": 400,
    # "rotating_proxies.middlewares.RotatingProxyMiddleware": 610,
    "rotating_proxies.middlewares.BanDetectionMiddleware":620,
    # "ecommerce_price_comparer.middlewares.GeonodeProxyMiddleware": 410,
}

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
#EXTENSIONS = {
#    "scrapy.extensions.telnet.TelnetConsole": None,
#}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
   "ecommerce_price_comparer.pipelines.EcommercePriceComparerPipeline": 300,
    "ecommerce_price_comparer.pipelines.SaveToPostgresSQLPipeline": 400,

}
#Where is your thing saved
# FEEDS = {
#     'products.json': {'format': 'json'},
#         }
# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
# The initial download delay
AUTOTHROTTLE_START_DELAY = 1
# The maximum download delay to be set in case of high latencies
AUTOTHROTTLE_MAX_DELAY = 15
# The average number of requests Scrapy should be sending in parallel to
# each remote server
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = "httpcache"
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]
# Set settings whose default value is deprecated to a future-proof value
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
#rotating proxy
ROTATING_PROXY_LIST =[
    '138.97.2.198:4145',
    '201.68.215.79:61221',
    '62.201.214.146:8080'
]
#FAKEUSER AGENT
SCRAPE_OPS_API_KEY = 'e8017e79-2dbb-4b9e-a9ce-6974dc387de2'
SCRAPE_OPS_FAKE_USER_AGENT_ENDPOINT = 'https://headers.scrapeops.io/v1/user-agents'
SCRAPE_OPS_FAKE_USER_AGENT_ENABLED = True
SCRAPE_OPS_NUM_RESULTS = 50
