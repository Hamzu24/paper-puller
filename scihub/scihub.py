# -*- coding: utf-8 -*-

"""
Sci-API Unofficial API
[Search|Download] research papers from [scholar.google.com|sci-hub.io].

@author zaytoun
"""

import re
import argparse
import hashlib
import json
import logging
import os
import unicodedata
import xml.etree.ElementTree as ET

import requests
import urllib3
from bs4 import BeautifulSoup
from retrying import retry

# log config
logging.basicConfig()
logger = logging.getLogger('Sci-Hub')
logger.setLevel(logging.DEBUG)

#
urllib3.disable_warnings()

# constants
SCHOLARS_BASE_URL = 'https://scholar.google.com/scholar'
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:27.0) Gecko/20100101 Firefox/27.0'}

class SciHub(object):
    """
    SciHub class can search for papers on Google Scholars
    and fetch/download papers from sci-hub.io
    """

    def __init__(self, use_open_access=True, unpaywall_email=None, openalex_api_key=None):
        self.sess = requests.Session()
        self.sess.headers = HEADERS
        self.available_base_url_list = self._get_available_scihub_urls()
        self.base_url = self.available_base_url_list[0] + '/'

        # Open access configuration
        self.use_open_access = use_open_access
        self.unpaywall_email = unpaywall_email or 'scihub-api@example.com'
        self.openalex_api_key = openalex_api_key

    def _get_available_scihub_urls(self):
        '''
        Finds available scihub urls via https://sci-hub.now.sh/
        Tests each URL and filters out those returning 403 (Cloudflare blocked).
        '''
        urls = []
        res = requests.get('https://sci-hub.now.sh/')
        s = self._get_soup(res.content)
        for a in s.find_all('a', href=True):
            if 'sci-hub.' in a['href']:
                urls.append(a['href'])

        # Test each URL and filter out blocked ones
        working_urls = []
        for url in urls:
            if self._test_scihub_url(url):
                working_urls.append(url)
                # Stop after finding first working URL to speed up initialization
                # Other URLs will be tested on-demand if this one fails
                break

        # Keep remaining untested URLs as fallbacks
        for url in urls:
            if url not in working_urls:
                working_urls.append(url)

        return working_urls if working_urls else urls

    def _test_scihub_url(self, url):
        '''
        Test if a Sci-Hub URL is accessible (not blocked by Cloudflare).
        Tests with a known DOI since Cloudflare may allow homepage but block paper pages.
        Returns True if accessible, False if blocked (403) or error.
        '''
        try:
            # Test with a known DOI - homepage may pass but paper pages get blocked
            test_url = url.rstrip('/') + '/10.1038/nature12373'
            res = self.sess.get(test_url, verify=False, timeout=10)
            if res.status_code == 403:
                logger.debug('Sci-Hub URL %s returned 403 (Cloudflare blocked)', url)
                return False
            # Also check for Cloudflare challenge page in response
            if 'Just a moment' in res.text or 'Cloudflare' in res.text:
                logger.debug('Sci-Hub URL %s has Cloudflare challenge', url)
                return False
            return True
        except Exception as e:
            logger.debug('Sci-Hub URL %s test failed: %s', url, e)
            return False

    def set_proxy(self, proxy):
        '''
        set proxy for session
        :param proxy_dict:
        :return:
        '''
        if proxy:
            self.sess.proxies = {
                "http": proxy,
                "https": proxy, }

    def _change_base_url(self):
        '''
        Switch to the next available Sci-Hub URL, skipping any that return 403.
        '''
        if not self.available_base_url_list:
            raise Exception('Ran out of valid sci-hub urls')

        # Remove current URL
        del self.available_base_url_list[0]

        # Find next working URL (skip 403 blocked ones)
        while self.available_base_url_list:
            candidate = self.available_base_url_list[0]
            if self._test_scihub_url(candidate):
                self.base_url = candidate + '/'
                logger.info("Switching to %s", candidate)
                return
            else:
                logger.debug("Skipping blocked URL: %s", candidate)
                del self.available_base_url_list[0]

        raise Exception('Ran out of valid sci-hub urls')

    def search(self, query, limit=10, download=False):
        """
        Performs a query on scholar.google.com, and returns a dictionary
        of results in the form {'papers': ...}. Unfortunately, as of now,
        captchas can potentially prevent searches after a certain limit.
        """
        start = 0
        results = {'papers': []}

        while True:
            try:
                res = self.sess.get(SCHOLARS_BASE_URL, params={'q': query, 'start': start})
            except requests.exceptions.RequestException as e:
                results['err'] = 'Failed to complete search with query %s (connection error)' % query
                return results

            s = self._get_soup(res.content)
            papers = s.find_all('div', class_="gs_r")

            if not papers:
                if 'CAPTCHA' in str(res.content):
                    results['err'] = 'Failed to complete search with query %s (captcha)' % query
                return results

            for paper in papers:
                if not paper.find('table'):
                    source = None
                    pdf = paper.find('div', class_='gs_ggs gs_fl')
                    link = paper.find('h3', class_='gs_rt')

                    if pdf:
                        source = pdf.find('a')['href']
                    elif link.find('a'):
                        source = link.find('a')['href']
                    else:
                        continue

                    results['papers'].append({
                        'name': link.text,
                        'url': source
                    })

                    if len(results['papers']) >= limit:
                        return results

            start += 10

    @retry(wait_random_min=100, wait_random_max=1000, stop_max_attempt_number=10)
    def download(self, identifier, destination='', path=None, title=None):
        """
        Downloads a paper from sci-hub given an indentifier (DOI, PMID, URL).
        Currently, this can potentially be blocked by a captcha if a certain
        limit has been reached.

        Args:
            identifier: DOI, PMID, or URL of the paper
            destination: Directory to save the paper
            path: Explicit filename to use (overrides title-based naming)
            title: Paper title for naming the file (spaces replaced with dashes)
        """
        data = self.fetch(identifier, title=title)

        if not 'err' in data:
            self._save(data['pdf'],
                       os.path.join(destination, path if path else data['name']))

        return data

    def fetch(self, identifier, title=None):
        """
        Fetches the paper by first retrieving the direct link to the pdf.
        If the indentifier is a DOI, PMID, or URL pay-wall, then use Sci-Hub
        to access and download paper. Otherwise, just download paper directly.

        Args:
            identifier: DOI, PMID, or URL of the paper
            title: Optional paper title for naming the downloaded file
                   (spaces will be replaced with dashes)
        """
        url = None  # Initialize to avoid undefined variable in exception handlers

        try:
            url = self._get_direct_url(identifier)

            if not url:
                return {
                    'err': 'Failed to find pdf url for identifier %s' % identifier
                }

            # verify=False is dangerous but sci-hub.io
            # requires intermediate certificates to verify
            # and requests doesn't know how to download them.
            # as a hacky fix, you can add them to your store
            # and verifying would work. will fix this later.
            res = self.sess.get(url, verify=False)

            if res.headers.get('Content-Type', '') != 'application/pdf':
                self._change_base_url()
                logger.info('Failed to fetch pdf with identifier %s '
                                           '(resolved url %s) due to captcha' % (identifier, url))

                # Try open access sources as fallback before giving up
                id_type = self._classify(identifier)
                if self.use_open_access and id_type == 'doi':
                    logger.debug('Trying open access sources after Sci-Hub captcha...')
                    pdf_url = self._try_open_access_sources(identifier)
                    if pdf_url:
                        logger.debug('Found open access URL: %s', pdf_url)
                        res = self.sess.get(pdf_url, verify=False)
                        if res.headers.get('Content-Type', '') == 'application/pdf':
                            return {
                                'pdf': res.content,
                                'url': pdf_url,
                                'name': self._generate_name(res, title=title)
                            }

                raise CaptchaNeedException('Failed to fetch pdf with identifier %s '
                                           '(resolved url %s) due to captcha' % (identifier, url))
            else:
                return {
                    'pdf': res.content,
                    'url': url,
                    'name': self._generate_name(res, title=title)
                }

        except requests.exceptions.ConnectionError:
            logger.info('Cannot access {}, changing url'.format(self.available_base_url_list[0]))
            self._change_base_url()
            return {
                'err': 'Connection error for identifier %s (resolved url %s), switched to new mirror. Please retry.'
                       % (identifier, url)
            }

        except requests.exceptions.RequestException as e:
            logger.info('Failed to fetch pdf with identifier %s (resolved url %s) due to request exception.'
                       % (identifier, url))
            return {
                'err': 'Failed to fetch pdf with identifier %s (resolved url %s) due to request exception.'
                       % (identifier, url)
            }

    def _get_direct_url(self, identifier, _recursion_depth=0):
        """
        Finds the direct source url for a given identifier.
        _recursion_depth is used internally to prevent infinite recursion.
        """
        MAX_RECURSION_DEPTH = 3
        if _recursion_depth >= MAX_RECURSION_DEPTH:
            logger.debug('Max recursion depth reached for identifier: %s', identifier)
            return None

        id_type = self._classify(identifier)

        if id_type == 'url-direct':
            return identifier
        elif id_type == 'arxiv':
            return self._get_arxiv_direct_url(identifier)
        elif id_type == 'biorxiv':
            return self._get_biorxiv_direct_url(identifier)
        elif id_type == 'medrxiv':
            return self._get_medrxiv_direct_url(identifier)
        elif id_type == 'biorxiv-or-medrxiv':
            return self._get_biorxiv_or_medrxiv_direct_url(identifier)
        elif id_type == 'psyarxiv':
            return self._get_psyarxiv_direct_url(identifier)
        elif id_type == 'pmid':
            return self._get_pmid_direct_url(identifier)
        else:
            # Try Sci-Hub first
            try:
                url = self._search_direct_url(identifier)
                if url:
                    return url
            except Exception as e:
                logger.debug('Sci-Hub lookup failed: %s', e)

            # Fall back to open access sources if Sci-Hub fails
            if self.use_open_access and id_type == 'doi':
                pdf_url = self._try_open_access_sources(identifier)
                if pdf_url:
                    return pdf_url

            # Try Google Scholar as last resort (treats identifier as search query)
            try:
                search_results = self.search(identifier, limit=1)
                if 'err' not in search_results and search_results.get('papers'):
                    paper_url = search_results['papers'][0]['url']
                    # Recursively get direct URL for the found paper
                    if paper_url != identifier:
                        logger.info('Found paper via Google Scholar: %s', paper_url)
                        return self._get_direct_url(paper_url, _recursion_depth + 1)
            except Exception as e:
                logger.debug('Google Scholar search failed: %s', e)

            return None

    def _get_arxiv_direct_url(self, identifier):
        """
        Convert arXiv identifier to direct PDF URL.
        Handles: arXiv:YYMM.NNNNN, YYMM.NNNNN, or arxiv.org URLs
        """
        # Extract arXiv ID from various formats
        if 'arxiv.org' in identifier.lower():
            # Handle URLs like https://arxiv.org/abs/2301.00001
            match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)', identifier, re.IGNORECASE)
            if match:
                arxiv_id = match.group(1)
            else:
                raise ValueError(f'Could not parse arXiv URL: {identifier}')
        elif identifier.lower().startswith('arxiv:'):
            arxiv_id = identifier[6:]  # Remove 'arXiv:' prefix
        else:
            arxiv_id = identifier

        return f'https://arxiv.org/pdf/{arxiv_id}.pdf'

    def _get_biorxiv_direct_url(self, identifier):
        """
        Convert bioRxiv identifier to direct PDF URL.
        Handles bioRxiv URLs.
        """
        if 'biorxiv.org' in identifier.lower():
            # Handle URLs like https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1
            match = re.search(r'biorxiv\.org/content/(10\.\d+/[\d.]+(?:v\d+)?)', identifier, re.IGNORECASE)
            if match:
                doi = match.group(1)
                # Ensure version suffix
                if not re.search(r'v\d+$', doi):
                    doi += 'v1'
                return f'https://www.biorxiv.org/content/{doi}.full.pdf'
            else:
                raise ValueError(f'Could not parse bioRxiv URL: {identifier}')
        else:
            raise ValueError(f'Invalid bioRxiv identifier: {identifier}')

    def _get_medrxiv_direct_url(self, identifier):
        """
        Convert medRxiv identifier to direct PDF URL.
        Handles medRxiv URLs.
        """
        if 'medrxiv.org' in identifier.lower():
            # Handle URLs like https://www.medrxiv.org/content/10.1101/2024.01.01.123456v1
            match = re.search(r'medrxiv\.org/content/(10\.\d+/[\d.]+(?:v\d+)?)', identifier, re.IGNORECASE)
            if match:
                doi = match.group(1)
                # Ensure version suffix
                if not re.search(r'v\d+$', doi):
                    doi += 'v1'
                return f'https://www.medrxiv.org/content/{doi}.full.pdf'
            else:
                raise ValueError(f'Could not parse medRxiv URL: {identifier}')
        else:
            raise ValueError(f'Invalid medRxiv identifier: {identifier}')

    def _get_biorxiv_or_medrxiv_direct_url(self, identifier):
        """
        Handle ambiguous 10.1101/ DOIs that could be bioRxiv or medRxiv.
        Try bioRxiv first, fall back to medRxiv if that fails.
        """
        doi = identifier
        # Ensure version suffix
        if not re.search(r'v\d+$', doi):
            doi_with_version = doi + 'v1'
        else:
            doi_with_version = doi

        # Try bioRxiv first
        biorxiv_url = f'https://www.biorxiv.org/content/{doi_with_version}.full.pdf'
        try:
            res = self.sess.head(biorxiv_url, verify=False, allow_redirects=True, timeout=10)
            if res.status_code == 200:
                return biorxiv_url
        except requests.exceptions.RequestException:
            pass

        # Fall back to medRxiv
        medrxiv_url = f'https://www.medrxiv.org/content/{doi_with_version}.full.pdf'
        return medrxiv_url

    def _get_psyarxiv_direct_url(self, identifier):
        """
        Extract PDF download URL from psyArXiv page.
        psyArXiv is hosted on OSF, so we need to fetch the page and find the download link.
        """
        if 'psyarxiv.com' not in identifier.lower():
            raise ValueError(f'Invalid psyArXiv identifier: {identifier}')

        # Ensure URL ends with download path
        if '/download' in identifier:
            return identifier

        # Fetch the psyArXiv page to find the PDF download link
        try:
            res = self.sess.get(identifier, verify=False)
            s = self._get_soup(res.content)

            # Look for the download link in the page
            # OSF pages typically have a download button/link
            download_link = s.find('a', {'data-analytics-name': 'Download'})
            if download_link and download_link.get('href'):
                href = download_link['href']
                if href.startswith('/'):
                    return 'https://psyarxiv.com' + href
                return href

            # Alternative: look for meta tag with PDF URL
            meta_pdf = s.find('meta', {'name': 'citation_pdf_url'})
            if meta_pdf and meta_pdf.get('content'):
                return meta_pdf['content']

            # Try appending /download to the URL
            clean_url = identifier.rstrip('/')
            return clean_url + '/download'

        except requests.exceptions.RequestException as e:
            logger.warning(f'Failed to fetch psyArXiv page: {e}')
            # Fallback: try appending /download
            clean_url = identifier.rstrip('/')
            return clean_url + '/download'

    def _get_pmid_direct_url(self, pmid):
        """
        Convert PMID to DOI via NCBI E-utilities API, then get direct URL.
        Also checks for free full text in PubMed Central (PMC).
        """
        # Query NCBI E-utilities to get article metadata including DOI
        url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml'
        try:
            res = self.sess.get(url, timeout=15)
            if res.status_code != 200:
                logger.debug('NCBI E-utilities returned status %d for PMID %s', res.status_code, pmid)
                return None

            # Parse XML to extract DOI and PMC ID
            root = ET.fromstring(res.content)

            # Look for DOI in ArticleIdList
            doi = None
            pmcid = None
            for article_id in root.findall('.//ArticleId'):
                id_type = article_id.get('IdType')
                if id_type == 'doi':
                    doi = article_id.text
                elif id_type == 'pmc':
                    pmcid = article_id.text

            # If we found a PMC ID, try to get the PDF from PMC first (free full text)
            if pmcid:
                logger.debug('Found PMC ID %s for PMID %s, trying PMC...', pmcid, pmid)
                pmc_url = self._get_pmc_pdf_url(pmcid)
                if pmc_url:
                    return pmc_url

            # If we have a DOI, recursively get the direct URL
            if doi:
                logger.debug('Found DOI %s for PMID %s', doi, pmid)
                return self._get_direct_url(doi)

            logger.debug('No DOI or PMC ID found for PMID %s', pmid)
            return None

        except ET.ParseError as e:
            logger.debug('Failed to parse NCBI response for PMID %s: %s', pmid, e)
            return None
        except requests.exceptions.RequestException as e:
            logger.debug('NCBI request failed for PMID %s: %s', pmid, e)
            return None

    def _get_pmc_pdf_url(self, pmcid):
        """
        Get PDF URL from PubMed Central OA service.
        pmcid can be with or without 'PMC' prefix.
        """
        # Normalize PMC ID (add PMC prefix if missing)
        if not pmcid.upper().startswith('PMC'):
            pmcid = 'PMC' + pmcid

        # Try the OA service first
        url = f'https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}'
        try:
            res = self.sess.get(url, timeout=15)
            if res.status_code == 200:
                # Parse XML response for PDF link
                root = ET.fromstring(res.content)
                # Look for PDF link in the response
                for link in root.findall('.//link'):
                    if link.get('format') == 'pdf':
                        href = link.get('href')
                        if href:
                            # Convert FTP URLs to HTTPS if needed
                            if href.startswith('ftp://'):
                                href = href.replace('ftp://ftp.ncbi.nlm.nih.gov', 'https://www.ncbi.nlm.nih.gov')
                            logger.debug('Found PMC PDF URL: %s', href)
                            return href

            # Fallback: construct direct PDF URL
            # PMC papers are often available at this URL pattern
            direct_url = f'https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/'
            try:
                head_res = self.sess.head(direct_url, verify=False, allow_redirects=True, timeout=10)
                if head_res.status_code == 200:
                    return direct_url
            except requests.exceptions.RequestException:
                pass

        except (ET.ParseError, requests.exceptions.RequestException) as e:
            logger.debug('PMC lookup failed for %s: %s', pmcid, e)

        return None

    def _try_open_access_sources(self, doi):
        """
        Try multiple open access sources to find a direct PDF URL.
        Returns the first successful PDF URL or None.

        Order of priority:
        1. Unpaywall (best OA coverage, no auth)
        2. Europe PMC (10.2M full text articles)
        3. Semantic Scholar (good coverage, has openAccessPdf field)
        4. OpenAlex (large coverage)
        5. CrossRef (OA links and metadata)
        6. CORE API (323M free full texts)
        7. Internet Archive Scholar (35M+ scholarly articles)
        """
        # Try Unpaywall first
        try:
            pdf_url = self._get_unpaywall_pdf_url(doi)
            if pdf_url and self._validate_pdf_url(pdf_url):
                logger.info('Found PDF via Unpaywall: %s', pdf_url)
                return pdf_url
        except Exception as e:
            logger.debug('Unpaywall lookup failed: %s', e)

        # Try Europe PMC (good for biomedical papers)
        try:
            pdf_url = self._search_europe_pmc(doi)
            if pdf_url and self._validate_pdf_url(pdf_url):
                logger.info('Found PDF via Europe PMC: %s', pdf_url)
                return pdf_url
        except Exception as e:
            logger.debug('Europe PMC lookup failed: %s', e)

        # Try Semantic Scholar
        try:
            pdf_url = self._get_semantic_scholar_pdf_url(doi)
            if pdf_url and self._validate_pdf_url(pdf_url):
                logger.info('Found PDF via Semantic Scholar: %s', pdf_url)
                return pdf_url
        except Exception as e:
            logger.debug('Semantic Scholar lookup failed: %s', e)

        # Try OpenAlex (if API key is configured or without it)
        try:
            pdf_url = self._get_openalex_pdf_url(doi)
            if pdf_url and self._validate_pdf_url(pdf_url):
                logger.info('Found PDF via OpenAlex: %s', pdf_url)
                return pdf_url
        except Exception as e:
            logger.debug('OpenAlex lookup failed: %s', e)

        # Try CrossRef for OA links
        try:
            pdf_url = self._get_crossref_pdf_url(doi)
            if pdf_url and self._validate_pdf_url(pdf_url):
                logger.info('Found PDF via CrossRef: %s', pdf_url)
                return pdf_url
        except Exception as e:
            logger.debug('CrossRef lookup failed: %s', e)

        # Try CORE API (large aggregator)
        try:
            pdf_url = self._search_core(doi)
            if pdf_url and self._validate_pdf_url(pdf_url):
                logger.info('Found PDF via CORE: %s', pdf_url)
                return pdf_url
        except Exception as e:
            logger.debug('CORE lookup failed: %s', e)

        # Try Internet Archive Scholar as last resort
        try:
            pdf_url = self._search_internet_archive(doi)
            if pdf_url and self._validate_pdf_url(pdf_url):
                logger.info('Found PDF via Internet Archive: %s', pdf_url)
                return pdf_url
        except Exception as e:
            logger.debug('Internet Archive lookup failed: %s', e)

        return None

    def _get_unpaywall_pdf_url(self, doi):
        """
        Query Unpaywall API to find open access PDF URL.
        API: https://api.unpaywall.org/v2/{doi}?email={email}
        Returns: best_oa_location.url_for_pdf or None
        """
        url = f'https://api.unpaywall.org/v2/{doi}?email={self.unpaywall_email}'
        res = self.sess.get(url, timeout=10)

        if res.status_code != 200:
            return None

        data = res.json()

        # Try best_oa_location first
        best_oa = data.get('best_oa_location')
        if best_oa:
            pdf_url = best_oa.get('url_for_pdf')
            if pdf_url:
                return pdf_url

        # Fall back to iterating oa_locations
        oa_locations = data.get('oa_locations', [])
        for loc in oa_locations:
            pdf_url = loc.get('url_for_pdf')
            if pdf_url:
                return pdf_url

        return None

    def _get_semantic_scholar_pdf_url(self, doi):
        """
        Query Semantic Scholar API to find open access PDF URL.
        API: https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=openAccessPdf
        Returns: openAccessPdf.url or None
        """
        url = f'https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=openAccessPdf'
        res = self.sess.get(url, timeout=10)

        if res.status_code != 200:
            return None

        data = res.json()
        open_access_pdf = data.get('openAccessPdf')
        if open_access_pdf:
            return open_access_pdf.get('url')

        return None

    def _get_openalex_pdf_url(self, doi):
        """
        Query OpenAlex API to find open access PDF URL.
        API: https://api.openalex.org/works/doi:{doi}
        Returns: best_oa_location.pdf_url or primary_location.pdf_url or None
        """
        url = f'https://api.openalex.org/works/doi:{doi}'

        headers = {}
        if self.openalex_api_key:
            headers['Authorization'] = f'Bearer {self.openalex_api_key}'

        res = self.sess.get(url, headers=headers, timeout=10)

        if res.status_code != 200:
            return None

        data = res.json()

        # Try best_oa_location first
        best_oa = data.get('best_oa_location')
        if best_oa:
            pdf_url = best_oa.get('pdf_url')
            if pdf_url:
                return pdf_url

        # Try primary_location
        primary = data.get('primary_location')
        if primary:
            pdf_url = primary.get('pdf_url')
            if pdf_url:
                return pdf_url

        # Fall back to iterating locations
        locations = data.get('locations', [])
        for loc in locations:
            pdf_url = loc.get('pdf_url')
            if pdf_url:
                return pdf_url

        return None

    def _search_europe_pmc(self, doi):
        """
        Search Europe PMC for open access full text.
        API: https://www.ebi.ac.uk/europepmc/webservices/rest/search
        Coverage: 10.2M full text articles
        """
        url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
        params = {
            'query': f'DOI:{doi}',
            'format': 'json',
            'resultType': 'core'
        }
        res = self.sess.get(url, params=params, timeout=15)

        if res.status_code != 200:
            return None

        data = res.json()
        results = data.get('resultList', {}).get('result', [])

        for result in results:
            # Check for full text URLs
            full_text_urls = result.get('fullTextUrlList', {}).get('fullTextUrl', [])
            for url_info in full_text_urls:
                if url_info.get('documentStyle') == 'pdf':
                    return url_info.get('url')

            # Check for PMC ID and construct URL
            pmcid = result.get('pmcid')
            if pmcid:
                pmc_url = self._get_pmc_pdf_url(pmcid)
                if pmc_url:
                    return pmc_url

        return None

    def _get_crossref_pdf_url(self, doi):
        """
        Query CrossRef API to find OA links and full-text URLs.
        API: https://api.crossref.org/works/{doi}
        Coverage: 150M+ DOIs with metadata
        """
        url = f'https://api.crossref.org/works/{doi}'
        headers = {
            'User-Agent': 'SciHub-API/1.0 (mailto:scihub-api@example.com)'
        }
        res = self.sess.get(url, headers=headers, timeout=15)

        if res.status_code != 200:
            return None

        data = res.json()
        work = data.get('message', {})

        # Check 'link' field for full-text URLs
        links = work.get('link', [])
        for link in links:
            content_type = link.get('content-type', '')
            url = link.get('URL', '')
            # Prefer PDF links
            if 'pdf' in content_type.lower() or url.lower().endswith('.pdf'):
                return url

        # Check for open access via license (Creative Commons, etc.)
        licenses = work.get('license', [])
        for license_info in licenses:
            license_url = license_info.get('URL', '')
            if 'creativecommons.org' in license_url or 'open' in license_url.lower():
                # Paper is likely OA, check for DOI resolution to PDF
                resource = work.get('resource', {})
                primary_url = resource.get('primary', {}).get('URL')
                if primary_url:
                    # Try to find PDF link at publisher site
                    return primary_url

        return None

    def _search_core(self, identifier):
        """
        Search CORE aggregator for paper.
        API: https://api.core.ac.uk/v3/
        Coverage: 431M metadata records, 323M free full texts
        Note: Free tier available, no API key required for basic searches.
        """
        url = 'https://api.core.ac.uk/v3/search/works'
        params = {
            'q': identifier,
            'limit': 5
        }
        headers = {
            'Accept': 'application/json'
        }

        res = self.sess.get(url, params=params, headers=headers, timeout=15)

        if res.status_code != 200:
            return None

        data = res.json()
        results = data.get('results', [])

        for result in results:
            # Check for downloadUrl (direct PDF link)
            download_url = result.get('downloadUrl')
            if download_url:
                return download_url

            # Check for fullTextLink
            full_text = result.get('fullText')
            if full_text:
                # CORE may include the full text directly, but we want PDF URL
                pass

            # Check links array
            links = result.get('links', [])
            for link in links:
                if isinstance(link, dict):
                    link_url = link.get('url', '')
                elif isinstance(link, str):
                    link_url = link
                else:
                    continue
                if link_url and (link_url.endswith('.pdf') or 'pdf' in link_url.lower()):
                    return link_url

        return None

    def _search_internet_archive(self, identifier):
        """
        Search Internet Archive Scholar for paper.
        Uses the Fatcat API which powers IA Scholar.
        Coverage: 35M+ scholarly articles
        """
        # Try Fatcat API for DOI lookup
        if '/' in identifier:  # Looks like a DOI
            url = f'https://api.fatcat.wiki/v0/release/lookup?doi={identifier}'
            res = self.sess.get(url, timeout=15)

            if res.status_code == 200:
                data = res.json()

                # Check for files with PDF URLs
                files = data.get('files', [])
                for file_info in files:
                    urls = file_info.get('urls', [])
                    for url_info in urls:
                        url = url_info.get('url', '')
                        if 'archive.org' in url or url.endswith('.pdf'):
                            return url

                # Check for release ident to construct IA Scholar URL
                release_ident = data.get('ident')
                if release_ident:
                    # Try to get the file directly
                    files_url = f'https://api.fatcat.wiki/v0/release/{release_ident}/files'
                    files_res = self.sess.get(files_url, timeout=15)
                    if files_res.status_code == 200:
                        files_data = files_res.json()
                        for file_info in files_data:
                            urls = file_info.get('urls', [])
                            for url_info in urls:
                                url = url_info.get('url', '')
                                if 'archive.org' in url:
                                    return url

        return None

    # Expanded stop words list for better title matching
    STOP_WORDS = {
        'a', 'an', 'the', 'of', 'and', 'for', 'in', 'on', 'to', 'with', 'by',
        'is', 'are', 'using', 'via', 'based', 'novel', 'new', 'towards',
        'study', 'analysis', 'method', 'approach', 'review', 'survey',
        'from', 'as', 'at', 'or', 'its', 'their', 'this', 'that', 'which',
        'we', 'our', 'can', 'be', 'been', 'being', 'have', 'has', 'had'
    }

    def _normalize_title(self, title):
        """
        Normalize a title for comparison:
        1. Normalize unicode accents (e.g., é -> e)
        2. Lowercase
        3. Keep only alphanumeric characters
        """
        # Normalize unicode accents (NFKD decomposition)
        normalized = unicodedata.normalize('NFKD', title)
        # Remove combining characters (accents)
        normalized = ''.join(c for c in normalized if not unicodedata.combining(c))
        # Lowercase and keep only alphanumeric
        return re.sub(r'[^a-z0-9]', '', normalized.lower())

    def _title_similarity(self, title1, title2, threshold=0.7):
        """
        Calculate word overlap similarity between two titles.
        Returns a score between 0 and 1.

        Args:
            title1: First title
            title2: Second title
            threshold: Minimum similarity threshold (not used in calculation,
                      but available for callers to check against)
        """
        # Normalize accents before extracting words
        norm1 = unicodedata.normalize('NFKD', title1)
        norm1 = ''.join(c for c in norm1 if not unicodedata.combining(c))
        norm2 = unicodedata.normalize('NFKD', title2)
        norm2 = ''.join(c for c in norm2 if not unicodedata.combining(c))

        words1 = set(re.findall(r'[a-z]+', norm1.lower()))
        words2 = set(re.findall(r'[a-z]+', norm2.lower()))

        # Remove stop words
        words1 = words1 - self.STOP_WORDS
        words2 = words2 - self.STOP_WORDS

        if not words1 or not words2:
            return 0

        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)

    def _search_by_title_arxiv(self, title):
        """
        Search for a paper by title using arXiv API.
        Returns dict with 'arxiv_id' if found, empty dict otherwise.
        Uses stricter threshold (0.70) since arXiv has many similar titles.
        """
        ARXIV_THRESHOLD = 0.70  # Strict threshold for arXiv (many similar titles)

        url = 'http://export.arxiv.org/api/query'
        # Use title-specific search for better matching
        params = {
            'search_query': f'ti:"{title}"',
            'max_results': 5
        }
        res = self.sess.get(url, params=params, timeout=15)

        if res.status_code != 200:
            return {}

        # Parse XML response
        root = ET.fromstring(res.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        # Find the best matching result by title similarity
        best_match = None
        best_score = 0
        for entry in root.findall('atom:entry', ns):
            title_el = entry.find('atom:title', ns)
            if title_el is None or not title_el.text:
                continue
            paper_title = ' '.join(title_el.text.split())  # Normalize whitespace
            score = self._title_similarity(title, paper_title)
            if score > best_score:
                best_score = score
                best_match = entry

        # Require threshold match for reliable results
        if best_match is None or best_score < ARXIV_THRESHOLD:
            return {}

        # Extract arXiv ID from the entry ID (e.g., http://arxiv.org/abs/1810.04805v2)
        id_el = best_match.find('atom:id', ns)
        if id_el is None or not id_el.text:
            return {}

        match = re.search(r'arxiv\.org/abs/(\d{4}\.\d{4,5})', id_el.text)
        if not match:
            return {}

        arxiv_id = match.group(1)
        title_el = best_match.find('atom:title', ns)
        paper_title = ' '.join(title_el.text.split()) if title_el is not None else 'Unknown'
        logger.debug('arXiv: Found paper with %.0f%% title match: %s', best_score * 100, paper_title)

        return {'arxiv_id': arxiv_id}

    def _search_by_title_openalex(self, title):
        """
        Search for a paper by title using OpenAlex API.
        Returns dict with 'doi', 'arxiv_id', and/or 'pdf_url' if found, empty dict otherwise.
        Uses more lenient threshold (0.60) since OpenAlex has good relevance ranking.
        """
        OPENALEX_THRESHOLD = 0.60  # More lenient for OpenAlex (good relevance ranking)

        url = 'https://api.openalex.org/works'
        params = {
            'filter': f'title.search:{title}',
            'per_page': 5,
            'sort': 'cited_by_count:desc'
        }
        res = self.sess.get(url, params=params, timeout=15)

        if res.status_code != 200:
            return {}

        data = res.json()
        results = data.get('results', [])

        # Find the best matching result by title similarity
        best_match = None
        best_score = 0
        for paper in results:
            paper_title = paper.get('title', '')
            if not paper_title:  # Skip papers with empty titles
                continue
            score = self._title_similarity(title, paper_title)
            if score > best_score:
                best_score = score
                best_match = paper

        # Require threshold match for reliable results
        if not best_match or best_score < OPENALEX_THRESHOLD:
            return {}

        logger.debug('OpenAlex: Found paper with %.0f%% title match: %s', best_score * 100, best_match.get('title', ''))

        paper = best_match
        result = {}

        # Check for arXiv ID in the ids field
        ids = paper.get('ids', {})
        for key, value in ids.items():
            if value and 'arxiv' in str(value).lower():
                match = re.search(r'arxiv[.:/](\d{4}\.\d{4,5})', str(value), re.IGNORECASE)
                if match:
                    result['arxiv_id'] = match.group(1)
                    break

        # Get DOI
        doi = paper.get('doi')
        if doi:
            result['doi'] = doi.replace('https://doi.org/', '')

        # Get Open Access PDF URL
        oa_url = paper.get('open_access', {}).get('oa_url')
        if oa_url:
            result['pdf_url'] = oa_url

        return result

    def _search_by_title_semantic_scholar(self, title):
        """
        Search for a paper by title using Semantic Scholar API.
        Returns dict with 'doi', 'arxiv_id', and/or 'pdf_url' if found, empty dict otherwise.
        Uses more lenient threshold (0.60) since Semantic Scholar has good relevance ranking.
        """
        SEMANTIC_SCHOLAR_THRESHOLD = 0.60  # More lenient (good relevance ranking)

        url = 'https://api.semanticscholar.org/graph/v1/paper/search'
        params = {
            'query': title,
            'limit': 5,
            'fields': 'title,externalIds,openAccessPdf'
        }
        res = self.sess.get(url, params=params, timeout=15)

        if res.status_code != 200:
            return {}

        data = res.json()
        results = data.get('data', [])

        # Find the best matching result by title similarity
        best_match = None
        best_score = 0
        for paper in results:
            paper_title = paper.get('title', '')
            if not paper_title:
                continue
            score = self._title_similarity(title, paper_title)
            if score > best_score:
                best_score = score
                best_match = paper

        # Require threshold match for reliable results
        if not best_match or best_score < SEMANTIC_SCHOLAR_THRESHOLD:
            return {}

        logger.debug('Semantic Scholar: Found paper with %.0f%% title match: %s', best_score * 100, best_match.get('title', ''))

        result = {}
        ext_ids = best_match.get('externalIds', {})

        # Get arXiv ID
        arxiv_id = ext_ids.get('ArXiv')
        if arxiv_id:
            result['arxiv_id'] = arxiv_id

        # Get DOI
        doi = ext_ids.get('DOI')
        if doi:
            result['doi'] = doi

        # Get Open Access PDF URL
        oa_pdf = best_match.get('openAccessPdf')
        if oa_pdf and oa_pdf.get('url'):
            result['pdf_url'] = oa_pdf['url']

        return result

    def _search_by_title(self, title):
        """
        Search for a paper by title using multiple APIs.
        Tries arXiv first (free, reliable), then OpenAlex, then Semantic Scholar.
        Returns dict with 'doi', 'arxiv_id', and/or 'pdf_url' if found, empty dict otherwise.
        """
        # Try arXiv first (free and reliable for CS/ML papers)
        try:
            result = self._search_by_title_arxiv(title)
            if result:
                return result
        except Exception as e:
            logger.debug('arXiv title search failed: %s', e)

        # Try OpenAlex (has DOIs and some arXiv IDs)
        try:
            result = self._search_by_title_openalex(title)
            if result:
                return result
        except Exception as e:
            logger.debug('OpenAlex title search failed: %s', e)

        # Fall back to Semantic Scholar
        try:
            result = self._search_by_title_semantic_scholar(title)
            if result:
                return result
        except Exception as e:
            logger.debug('Semantic Scholar title search failed: %s', e)

        return {}

    def _validate_pdf_url(self, url):
        """
        Verify URL points to an actual PDF by checking Content-Type header.
        Returns True if Content-Type contains 'application/pdf', or if the URL
        ends with .pdf and the HEAD request fails or returns a non-200 status
        (some servers block HEAD requests or require authentication).
        """
        try:
            res = self.sess.head(url, verify=False, allow_redirects=True, timeout=10)
            content_type = res.headers.get('Content-Type', '')

            # If we get a valid PDF content type, accept it
            if 'application/pdf' in content_type.lower():
                return True

            # If status is not 200, the server might be blocking HEAD requests
            # Accept URLs ending in .pdf as potentially valid
            if res.status_code != 200 and url.lower().endswith('.pdf'):
                return True

            # If we get HTML or other non-PDF content type with 200 status, reject it
            return False
        except Exception:
            # If HEAD fails, assume it might be valid and let fetch try
            return True

    def _search_direct_url(self, identifier):
        """
        Sci-Hub embeds papers in an iframe. This function finds the actual
        source url which looks something like https://moscow.sci-hub.io/.../....pdf.
        """
        res = self.sess.get(self.base_url + identifier, verify=False)
        s = self._get_soup(res.content)
        iframe = s.find('iframe')
        if iframe:
            return iframe.get('src') if not iframe.get('src').startswith('//') \
                else 'http:' + iframe.get('src')
        else:
            logger.debug('No iframe found on Sci-Hub page for identifier: %s (URL: %s)',
                        identifier, self.base_url + identifier)

    def _classify(self, identifier):
        """
        Classify the type of identifier:
        url-direct - openly accessible paper
        url-non-direct - pay-walled paper
        pmid - PubMed ID
        doi - digital object identifier
        arxiv - arXiv preprint
        biorxiv - bioRxiv preprint
        medrxiv - medRxiv preprint
        psyarxiv - psyArXiv preprint
        """
        # Check for arXiv identifiers
        # Patterns: arXiv:YYMM.NNNNN, YYMM.NNNNN, or arxiv.org URLs
        if identifier.lower().startswith('arxiv:'):
            return 'arxiv'
        if re.match(r'^\d{4}\.\d{4,5}(v\d+)?$', identifier):
            return 'arxiv'
        if 'arxiv.org' in identifier.lower():
            return 'arxiv'

        # Check for psyArXiv (before bioRxiv/medRxiv since it uses OSF)
        if 'psyarxiv.com' in identifier.lower():
            return 'psyarxiv'

        # Check for bioRxiv URLs
        if 'biorxiv.org' in identifier.lower():
            return 'biorxiv'

        # Check for medRxiv URLs
        if 'medrxiv.org' in identifier.lower():
            return 'medrxiv'

        # Check for 10.1101/ DOIs (bioRxiv/medRxiv)
        # These are ambiguous, we'll try bioRxiv first in _get_direct_url
        if identifier.startswith('10.1101/'):
            return 'biorxiv-or-medrxiv'

        if (identifier.startswith('http') or identifier.startswith('https')):
            if identifier.endswith('pdf'):
                return 'url-direct'
            else:
                return 'url-non-direct'
        elif identifier.isdigit():
            return 'pmid'
        else:
            return 'doi'

    def _save(self, data, path):
        """
        Save a file give data and a path.
        """
        with open(path, 'wb') as f:
            f.write(data)

    def _get_soup(self, html):
        """
        Return html soup.
        """
        return BeautifulSoup(html, 'html.parser')

    def _sanitize_filename(self, title, max_length=100):
        """
        Convert a title to a safe filename.
        - Replaces spaces with dashes
        - Removes or replaces unsafe characters
        - Truncates to max_length
        """
        # Normalize unicode
        title = unicodedata.normalize('NFKD', title)
        title = ''.join(c for c in title if not unicodedata.combining(c))

        # Replace spaces and underscores with dashes
        filename = re.sub(r'[\s_]+', '-', title)

        # Remove characters that are unsafe for filenames
        # Keep alphanumeric, dashes, dots, and parentheses
        filename = re.sub(r'[^\w\-\.\(\)]', '', filename)

        # Collapse multiple dashes
        filename = re.sub(r'-+', '-', filename)

        # Remove leading/trailing dashes
        filename = filename.strip('-')

        # Truncate if too long (leave room for .pdf extension)
        if len(filename) > max_length:
            filename = filename[:max_length].rstrip('-')

        return filename

    def _generate_name(self, res, title=None):
        """
        Generate filename for paper.

        If title is provided, uses sanitized title as filename.
        Otherwise, falls back to MD5 hash + last 20 chars of URL.
        """
        if title:
            # Use title-based naming
            filename = self._sanitize_filename(title)
            if not filename:
                # Fall back to hash if sanitization results in empty string
                filename = hashlib.md5(res.content).hexdigest()[:16]
        else:
            # Legacy hash-based naming
            name = res.url.split('/')[-1]
            name = re.sub('#view=(.+)', '', name)
            pdf_hash = hashlib.md5(res.content).hexdigest()
            filename = '%s-%s' % (pdf_hash, name[-20:])

        # Ensure .pdf extension
        if not filename.lower().endswith('.pdf'):
            filename += '.pdf'
        return filename

    def download_from_json(self, json_path, category, output_dir):
        """
        Download papers from a specific category in the JSON file.

        Args:
            json_path: Path to JSON file
            category: Key name in the JSON dict to download
            output_dir: Directory to save PDFs and log file

        JSON format: {"category_name": ["Title 1", "Title 2"], ...}

        Returns:
            List of result dicts with 'title', 'status', and 'filename' or 'error'
        """
        # Create output directory if needed
        os.makedirs(output_dir, exist_ok=True)

        # Setup file logging
        log_path = os.path.join(output_dir, 'download.log')
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
        logger.addHandler(file_handler)

        # Load JSON and extract category
        with open(json_path, 'r') as f:
            data = json.load(f)

        titles = data[category]
        print(f"Downloading {len(titles)} papers from '{category}'")

        results = []
        for i, title in enumerate(titles, 1):
            print(f"[{i}/{len(titles)}] Searching: {title}")

            # First try OpenAlex to find arXiv ID, DOI and/or PDF URL
            search_result = self._search_by_title(title)
            identifier = None

            if search_result.get('arxiv_id'):
                print(f"  Found arXiv: {search_result['arxiv_id']}")
                identifier = f"arXiv:{search_result['arxiv_id']}"
            elif search_result.get('pdf_url'):
                print(f"  Found PDF URL: {search_result['pdf_url']}")
                identifier = search_result['pdf_url']
            elif search_result.get('doi'):
                print(f"  Found DOI: {search_result['doi']}")
                identifier = search_result['doi']
            else:
                # Fall back to Google Scholar
                print("  No results from OpenAlex, trying Google Scholar...")
                scholar_results = self.search(title, limit=1)

                if 'err' not in scholar_results and scholar_results.get('papers'):
                    paper_info = scholar_results['papers'][0]
                    print(f"  Found: {paper_info['name']}")
                    identifier = paper_info['url']

            if not identifier:
                msg = f"{title} | FAILED | No results found"
                print(f"  ✗ {msg}")
                logger.info(msg)
                results.append({'title': title, 'status': 'failed', 'error': 'No results'})
                continue

            print(f"  Downloading...")

            try:
                # If we have a direct PDF URL from OpenAlex, try fetching it directly first
                if search_result.get('pdf_url') and identifier == search_result['pdf_url']:
                    try:
                        res = self.sess.get(identifier, verify=False, timeout=30)
                        if res.status_code == 200 and 'application/pdf' in res.headers.get('Content-Type', ''):
                            # Generate filename using title (spaces replaced with dashes)
                            filename = self._sanitize_filename(title) + '.pdf'
                            filepath = os.path.join(output_dir, filename)
                            with open(filepath, 'wb') as f:
                                f.write(res.content)
                            msg = f"{title} | SUCCESS | Saved as {filename}"
                            print(f"  ✓ {msg}")
                            logger.info(msg)
                            results.append({'title': title, 'status': 'success', 'filename': filename})
                            continue
                    except Exception as e:
                        logger.debug('Direct PDF fetch failed, trying normal download: %s', e)
                    # If direct fetch fails, fall back to arXiv or DOI if available
                    if search_result.get('arxiv_id'):
                        identifier = f"arXiv:{search_result['arxiv_id']}"
                        print(f"  Direct PDF failed, trying arXiv: {identifier}")
                    elif search_result.get('doi'):
                        identifier = search_result['doi']
                        print(f"  Direct PDF failed, trying DOI: {identifier}")

                result = self.download(identifier, output_dir, title=title)
                if 'err' in result:
                    msg = f"{title} | FAILED | {result['err']}"
                    print(f"  ✗ {msg}")
                    logger.info(msg)
                    results.append({'title': title, 'status': 'failed', 'error': result['err']})
                else:
                    msg = f"{title} | SUCCESS | Saved as {result['name']}"
                    print(f"  ✓ {msg}")
                    logger.info(msg)
                    results.append({'title': title, 'status': 'success', 'filename': result['name']})
            except Exception as e:
                msg = f"{title} | FAILED | {str(e)}"
                print(f"  ✗ {msg}")
                logger.info(msg)
                results.append({'title': title, 'status': 'failed', 'error': str(e)})

        # Summary
        success = sum(1 for r in results if r['status'] == 'success')
        print(f"\nCompleted: {success}/{len(titles)} papers downloaded")
        print(f"Log saved to: {log_path}")

        # Remove the file handler to avoid duplicate logging on subsequent calls
        logger.removeHandler(file_handler)
        file_handler.close()

        return results


class CaptchaNeedException(Exception):
    pass

def main():
    sh = SciHub()

    parser = argparse.ArgumentParser(description='SciHub - To remove all barriers in the way of science.')
    parser.add_argument('category', nargs='?', default=None,
                        help='Category name from papers.json to download')
    parser.add_argument('--list', action='store_true',
                        help='List available categories in papers.json')
    parser.add_argument('-d', '--download', metavar='(DOI|PMID|URL)', help='tries to find and download the paper',
                        type=str)
    parser.add_argument('-f', '--file', metavar='path', help='pass file with list of identifiers and download each',
                        type=str)
    parser.add_argument('-s', '--search', metavar='query', help='search Google Scholars', type=str)
    parser.add_argument('-sd', '--search_download', metavar='query',
                        help='search Google Scholars and download if possible', type=str)
    parser.add_argument('-l', '--limit', metavar='N', help='the number of search results to limit to', default=10,
                        type=int)
    parser.add_argument('-o', '--output', metavar='path', help='directory to store papers', default='papers/', type=str)
    parser.add_argument('-v', '--verbose', help='increase output verbosity', action='store_true')
    parser.add_argument('-p', '--proxy', help='via proxy format like socks5://user:pass@host:port', action='store', type=str)

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    if args.proxy:
        sh.set_proxy(args.proxy)

    # Default behavior: batch download from papers.json
    if args.category or args.list:
        json_path = 'papers.json'

        if not os.path.exists(json_path):
            print(f"Error: {json_path} not found")
            return

        with open(json_path, 'r') as f:
            data = json.load(f)

        if args.list:
            print("Available categories:")
            for key, titles in data.items():
                print(f"  {key} ({len(titles)} papers)")
            return

        if args.category not in data:
            print(f"Error: Category '{args.category}' not found")
            print("Available:", ', '.join(data.keys()))
            return

        sh.download_from_json(json_path, args.category, args.output)

    elif args.download:
        result = sh.download(args.download, args.output)
        if 'err' in result:
            logger.debug('%s', result['err'])
        else:
            logger.debug('Successfully downloaded file with identifier %s', args.download)
    elif args.search:
        results = sh.search(args.search, args.limit)
        if 'err' in results:
            logger.debug('%s', results['err'])
        else:
            logger.debug('Successfully completed search with query %s', args.search)
        print(results)
    elif args.search_download:
        results = sh.search(args.search_download, args.limit)
        if 'err' in results:
            logger.debug('%s', results['err'])
        else:
            logger.debug('Successfully completed search with query %s', args.search_download)
            for paper in results['papers']:
                result = sh.download(paper['url'], args.output)
                if 'err' in result:
                    logger.debug('%s', result['err'])
                else:
                    logger.debug('Successfully downloaded file with identifier %s', paper['url'])
    elif args.file:
        with open(args.file, 'r') as f:
            identifiers = f.read().splitlines()
            for identifier in identifiers:
                result = sh.download(identifier, args.output)
                if 'err' in result:
                    logger.debug('%s', result['err'])
                else:
                    logger.debug('Successfully downloaded file with identifier %s', identifier)


if __name__ == '__main__':
    main()
