# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html

from scrapy import signals
from scrapy.exceptions import IgnoreRequest
from ecommerce_price_comparer.utilities.url_safety import is_public_url
from random import choice
from urllib.parse import urlencode
from random import randint
import requests

# useful for handling different item types with a single interface
from itemadapter import is_item, ItemAdapter


class EcommercePriceComparerSpiderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the spider middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        # Called for each response that goes through the spider
        # middleware and into the spider.

        # Should return None or raise an exception.
        return None

    def process_spider_output(self, response, result, spider):
        # Called with the results returned from the Spider, after
        # it has processed the response.

        # Must return an iterable of Request, or item objects.
        for i in result:
            yield i

    def process_spider_exception(self, response, exception, spider):
        # Called when a spider or process_spider_input() method
        # (from other spider middleware) raises an exception.

        # Should return either None or an iterable of Request or item objects.
        pass

    def process_start_requests(self, start_requests, spider):
        # Called with the start requests of the spider, and works
        # similarly to the process_spider_output() method, except
        # that it doesn’t have a response associated.

        # Must return only requests (not items).
        for r in start_requests:
            yield r

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)


class EcommercePriceComparerDownloaderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the downloader middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        # Called for each request that goes through the downloader
        # middleware.

        # Must either:
        # - return None: continue processing this request
        # - or return a Response object
        # - or return a Request object
        # - or raise IgnoreRequest: process_exception() methods of
        #   installed downloader middleware will be called
        return None

    def process_response(self, request, response, spider):
        # Called with the response returned from the downloader.

        # Must either;
        # - return a Response object
        # - return a Request object
        # - or raise IgnoreRequest
        return response

    def process_exception(self, request, exception, spider):
        # Called when a download handler or a process_request()
        # (from other downloader middleware) raises an exception.

        # Must either:
        # - return None: continue processing this exception
        # - return a Response object: stops process_exception() chain
        # - return a Request object: stops process_exception() chain
        pass

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)




class FakeUserAgentMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)
    def __init__(self, settings):
        self.scrape_ops_api_key = settings.get("SCRAPE_OPS_API_KEY")
        self.scrape_ops_endpoint= settings.get("SCRAPE_OPS_FAKE_USER_AGENT_ENDPOINT",'http://headers.scrapeops.io/v1/browser-headers')
        self.scrape_ops_fake_user_agents_enabled = settings.get("SCRAPE_OPS_FAKE_USER_AGENT_ENABLED",True)
        self.scrape_ops_num_results = settings.get("SCRAPE_OPS_NUM_RESULTS")
        self.headers_list = []
        self._get_user_agents_list()
        self._scrape_ops_fake_user_agents_enabled()
    def _get_user_agents_list(self):
        payload = {'api_key': self.scrape_ops_api_key}
        if self.scrape_ops_num_results is not None:
            payload['num_results'] = self.scrape_ops_num_results
        response = requests.get(self.scrape_ops_endpoint, params=urlencode(payload))
        json_response = response.json()
        self.user_agents_list = json_response.get('result',[])
    def _get_user_agents(self):
        random_index = randint(0,len(self.user_agents_list)-1)
        return self.user_agents_list[random_index]
    def _scrape_ops_fake_user_agents_enabled(self):
        if self.scrape_ops_api_key is None or self.scrape_ops_api_key is "" or self.scrape_ops_fake_user_agents_enabled == False:
            self.scrape_ops_fake_user_agents_enabled = False
        else:
            self.scrape_ops_fake_user_agents_enabled = True
    def process_request(self, request, spider):
        random_user_agent = self._get_user_agents()
        request.headers["User-Agent"] = random_user_agent






class SmartGeonodeProxyMiddleware:
    def __init__(self):
        self.proxies = []

    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def spider_opened(self, spider):
        spider.logger.info("Fetching 15 fresh proxies from Geonode as backup...")
        try:
            response = requests.get(
                'https://proxylist.geonode.com/api/proxy-list?limit=15&protocols=http%2Chttps&sort_by=lastChecked&sort_type=desc')
            data = response.json()

            for item in data.get('data', []):
                ip = item['ip']
                port = item['port']
                protocol = item['protocols'][0]
                proxy_url = f"{protocol}://{ip}:{port}"
                self.proxies.append(proxy_url)

            spider.logger.info(f"Successfully loaded {len(self.proxies)} backup proxies.")
        except Exception as e:
            spider.logger.error(f"Failed to fetch proxies: {e}")

    def process_request(self, request, spider):
        return None

    def process_response(self, request, response, spider):
        if response.status in [403, 429, 503]:
            if self.proxies:
                proxy = choice(self.proxies)
                request.meta['proxy'] = proxy
                request.dont_filter = True #
                spider.logger.warning(f"Banned (Status {response.status}). Retrying with proxy: {proxy}")
                return request
        return response

    def process_exception(self, request, exception, spider):
        if self.proxies:
            proxy = choice(self.proxies)
            request.meta['proxy'] = proxy
            request.dont_filter = True
            spider.logger.warning(f"Connection failed ({exception}). Switching to proxy: {proxy}")
            return request



class BlockInternalRequestsMiddleware:
    def process_request(self, request, spider):
        if not is_public_url(request.url):
            raise IgnoreRequest(f"Niedozwolony adres: {request.url}")
        return None
