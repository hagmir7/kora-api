from django.shortcuts import render
from django.http import JsonResponse
from django.views import View
from django.core.cache import cache
from django.core.files.base import ContentFile
from bs4 import BeautifulSoup
import requests
import random
import logging
from datetime import datetime, timedelta
from main.models import Competition, Country
from django.utils.text import slugify
import re
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# In-memory cache
_cache = {}
CACHE_DURATION = 3600  # 1 hour in seconds


class ScrapeCompetitionsView(View):
    """
    View to scrape competitions using ScraperAPI
    """

    SCRAPER_API_KEY = [
        "d0a075b5a0bcdefcaabdd16757067f0f",
        "2d118deea43615910a5ed5cc6f8f56fa",
        "4c64b63e0a8ebf500aaa60c2514e6e8f",
        "d0bae26ea5465f92fbe33e3fa3d2e850",
        "b9573123f02ebb9fdfcd6f4cace5fda5",
        "6c49e45c1661f242378ca489f92b6ede",
    ]

    def get(self, request):
        """
        GET request to scrape and save competitions
        """
        url = "https://jdwel.com/competitions/"
        use_js = request.GET.get("use_js", "false").lower() == "true"
        force_refresh = request.GET.get("force", "false").lower() == "true"
        download_images = request.GET.get("download_images", "true").lower() == "true"

        try:
            # Fetch page content using ScraperAPI
            html_content = self._fetch_with_scraperapi(url, use_js, force_refresh)

            if not html_content:
                return JsonResponse(
                    {"status": "error", "message": "Failed to fetch page content"},
                    status=500,
                )

            # Parse HTML
            soup = BeautifulSoup(html_content, "html.parser")

            # Find all competition divs
            competitions = soup.find_all("div", class_="comp_single")

            if not competitions:
                return JsonResponse(
                    {"status": "error", "message": "No competitions found on the page"},
                    status=404,
                )

            saved_count = 0
            updated_count = 0
            skipped_count = 0
            errors = []
            details = []
            image_errors = []

            for comp_div in competitions:
                try:
                    # Extract data
                    result = self._extract_competition_data(comp_div)

                    if not result:
                        skipped_count += 1
                        continue

                    # Use comp_id as unique identifier instead of slug
                    comp_id = result["code"]

                    # Create or update competition using code as unique field
                    competition, created = Competition.objects.update_or_create(
                        code=comp_id,  # Use code instead of slug for uniqueness
                        defaults={
                            "name": result["name"],
                            "title": result["title"],
                            "country": result["country"],
                            "type": result["type"],
                            "season": result["season"],
                            "description": result["description"],
                            "slug": result["slug"],
                            "body": result["body"],
                        },
                    )

                    # Download and save logo if enabled
                    if download_images and result.get("logo_url"):
                        image_success = self._download_and_save_logo(
                            competition,
                            result["logo_url"],
                            result.get("logo_filename", f"{comp_id}.png"),
                        )

                        if not image_success:
                            image_errors.append(
                                {
                                    "competition": competition.name,
                                    "url": result["logo_url"],
                                }
                            )

                    if created:
                        saved_count += 1
                        logger.info(
                            f"Created competition: {competition.name} ({comp_id})"
                        )
                        details.append(
                            {
                                "action": "created",
                                "name": competition.name,
                                "code": comp_id,
                                "slug": competition.slug,
                                "logo": (
                                    competition.logo.url if competition.logo else None
                                ),
                            }
                        )
                    else:
                        updated_count += 1
                        logger.info(
                            f"Updated competition: {competition.name} ({comp_id})"
                        )
                        details.append(
                            {
                                "action": "updated",
                                "name": competition.name,
                                "code": comp_id,
                                "slug": competition.slug,
                                "logo": (
                                    competition.logo.url if competition.logo else None
                                ),
                            }
                        )

                except Exception as e:
                    error_msg = f"Error processing competition: {str(e)}"
                    logger.error(error_msg)
                    errors.append(
                        {"competition": comp_div.get("id", "unknown"), "error": str(e)}
                    )

            # Get final count from database
            total_in_db = Competition.objects.count()
            comps_with_logos = Competition.objects.exclude(logo="").count()

            return JsonResponse(
                {
                    "status": "success",
                    "saved": saved_count,
                    "updated": updated_count,
                    "skipped": skipped_count,
                    "total_processed": len(competitions),
                    "total_in_database": total_in_db,
                    "competitions_with_logos": comps_with_logos,
                    "image_errors": len(image_errors),
                    "errors": errors,
                    "sample_details": details[:10],  # First 10 for debugging
                    "sample_image_errors": image_errors[:5],  # First 5 image errors
                }
            )

        except Exception as e:
            logger.error(f"Scraping error: {str(e)}")
            return JsonResponse({"status": "error", "message": str(e)}, status=500)

    def _fetch_with_scraperapi(self, url, use_js=False, force_refresh=False):
        """
        Fetch URL content using ScraperAPI with caching
        """
        # Check cache first
        cache_key = f"scraper_{url}_{use_js}"

        if not force_refresh:
            # Try Django cache first
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.info(f"Django cache hit for {url}")
                return cached_data

            # Check in-memory cache
            if cache_key in _cache:
                cached_data, cached_time = _cache[cache_key]
                if (datetime.now() - cached_time).seconds < CACHE_DURATION:
                    logger.info(f"Memory cache hit for {url}")
                    return cached_data

        # Fetch from ScraperAPI
        api_url = "http://api.scraperapi.com"
        params = {
            "api_key": random.choice(self.SCRAPER_API_KEY),
            "url": url,
            "render": "true" if use_js else "false",
        }

        try:
            logger.info(f"Fetching {url} via ScraperAPI (render={use_js})")
            response = requests.get(api_url, params=params, timeout=60)
            response.raise_for_status()

            html_content = response.text

            # Cache the result
            _cache[cache_key] = (html_content, datetime.now())
            cache.set(cache_key, html_content, CACHE_DURATION)

            logger.info(f"Successfully fetched and cached {url}")
            return html_content

        except requests.exceptions.RequestException as e:
            logger.error(f"ScraperAPI request failed: {str(e)}")
            return None

    def _download_and_save_logo(self, competition, logo_url, filename):
        """
        Download logo image and save it to competition
        """
        try:
            # Handle relative URLs
            if logo_url.startswith("/"):
                logo_url = f"https://jdwel.com{logo_url}"

            # Skip if logo already exists
            if competition.logo:
                logger.info(f"Logo already exists for {competition.name}")
                return True

            # Download image
            logger.info(f"Downloading logo from {logo_url}")
            response = requests.get(
                logo_url,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
            )
            response.raise_for_status()

            # Get filename from URL if not provided
            if not filename:
                parsed_url = urlparse(logo_url)
                filename = os.path.basename(parsed_url.path)

            # Ensure filename has extension
            if not os.path.splitext(filename)[1]:
                # Try to determine from content-type
                content_type = response.headers.get("content-type", "")
                if "png" in content_type:
                    filename += ".png"
                elif "jpg" in content_type or "jpeg" in content_type:
                    filename += ".jpg"
                elif "svg" in content_type:
                    filename += ".svg"
                else:
                    filename += ".png"  # Default

            # Save image to competition
            competition.logo.save(filename, ContentFile(response.content), save=True)

            logger.info(f"Successfully saved logo for {competition.name}: {filename}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download logo from {logo_url}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error saving logo for {competition.name}: {str(e)}")
            return False

    def _extract_competition_data(self, comp_div):
        """
        Extract competition data from HTML div
        """
        try:
            # Extract basic info
            comp_id = comp_div.get("id", "").strip()
            data_keys = comp_div.get("data-keys", "").strip()

            if not comp_id:
                logger.warning("Competition without ID found, skipping")
                return None

            # Extract names (Arabic and English)
            title_span = comp_div.find("h2", class_="title")
            if not title_span:
                logger.warning(f"No title found for {comp_id}")
                return None

            spans = title_span.find_all("span", recursive=False)

            if not spans:
                logger.warning(f"No span elements found for {comp_id}")
                return None

            arabic_name = spans[0].text.strip() if spans else ""

            # Get subtitle
            sub_title_elem = title_span.find("span", class_="sub_title")
            if sub_title_elem:
                # Get the first span inside sub_title
                sub_span = sub_title_elem.find("span")
                subtitle = sub_span.text.strip() if sub_span else ""
            else:
                subtitle = ""

            # Clean subtitle
            if subtitle == "-" or not subtitle:
                subtitle = ""

            # Extract English name from data-keys
            english_name = self._extract_english_name(data_keys)

            # Extract competition URL slug from href
            link = comp_div.find("a")
            comp_url = link.get("href", "") if link else ""

            # Extract slug from URL like: /competition/cecafa-kagame-cup/
            url_parts = [part for part in comp_url.strip("/").split("/") if part]
            url_slug = url_parts[-1] if url_parts else comp_id

            # Extract logo URL and filename
            logo_url = ""
            logo_filename = ""
            img = comp_div.find("img", class_="comp_logo")
            if img:
                logo_url = img.get("src", "")
                logo_alt = img.get("alt", "")

                # Get filename from src
                if logo_url:
                    parsed_url = urlparse(logo_url)
                    logo_filename = os.path.basename(parsed_url.path)

            # Determine competition type
            comp_type = self._determine_comp_type(arabic_name, english_name)

            # Try to find or create country
            country = self._extract_country(arabic_name, english_name)

            # Full title with subtitle
            if subtitle:
                full_title = f"{arabic_name} {subtitle}"
            else:
                full_title = arabic_name

            # Use English name if available, otherwise Arabic
            display_name = english_name if english_name else arabic_name

            # Create description
            if english_name and arabic_name and english_name != arabic_name:
                description = f"{arabic_name} ({english_name})"
            else:
                description = arabic_name

            return {
                "slug": url_slug,
                "name": display_name,
                "title": full_title,
                "country": country,
                "type": comp_type,
                "season": "2024-2025",
                "description": description,
                "code": comp_id,
                "body": data_keys,
                "logo_url": logo_url,
                "logo_filename": logo_filename,
            }

        except Exception as e:
            logger.error(f"Error extracting competition data: {str(e)}")
            return None

    def _extract_english_name(self, data_keys):
        """
        Extract English name from data-keys attribute
        """
        if not data_keys:
            return ""

        # Remove Arabic text first
        # Split and keep only Latin characters
        parts = data_keys.split()
        english_parts = []

        for part in parts:
            # Check if part contains only Latin characters, numbers, and common symbols
            if re.match(r"^[A-Za-z0-9\-&\.\'\s]+$", part):
                english_parts.append(part)

        english_name = " ".join(english_parts).strip()

        # Clean up common patterns
        english_name = re.sub(r"\s+", " ", english_name)

        return english_name

    def _determine_comp_type(self, arabic_name, english_name):
        """
        Determine competition type based on name
        """
        name_lower = (arabic_name + " " + english_name).lower()

        if "كأس" in arabic_name or "cup" in name_lower:
            if "سوبر" in arabic_name or "super" in name_lower:
                return "SuperCup"
            return "Cup"
        elif "دوري" in arabic_name or "league" in name_lower or "liga" in name_lower:
            return "League"
        elif (
            "تصفيات" in arabic_name
            or "qualification" in name_lower
            or "qualifiers" in name_lower
        ):
            return "Qualifier"
        elif (
            "ملحق" in arabic_name or "playoff" in name_lower or "play-off" in name_lower
        ):
            return "Playoff"
        elif (
            "بطولة" in arabic_name
            or "championship" in name_lower
            or "tournament" in name_lower
        ):
            if "قارات" in arabic_name or "continental" in name_lower:
                return "Continental"
            return "Tournament"
        elif "أمم" in arabic_name or "nations" in name_lower:
            return "International"
        elif "ودية" in arabic_name or "friendly" in name_lower:
            return "Friendly"

        return "League"

    def _extract_country(self, arabic_name, english_name):
        """
        Extract country from competition name
        """
        country_mapping = {
            # Arabic to Country
            "السعودي": "Saudi Arabia",
            "سعودي": "Saudi Arabia",
            "المصري": "Egypt",
            "مصر": "Egypt",
            "الإنجليزي": "England",
            "إنجليزي": "England",
            "الإسباني": "Spain",
            "إسباني": "Spain",
            "الإيطالي": "Italy",
            "إيطالي": "Italy",
            "الفرنسي": "France",
            "فرنسي": "France",
            "الألماني": "Germany",
            "ألماني": "Germany",
            "المغربي": "Morocco",
            "مغربي": "Morocco",
            "الجزائري": "Algeria",
            "جزائري": "Algeria",
            "التونسي": "Tunisia",
            "تونسي": "Tunisia",
            "الإماراتي": "UAE",
            "إماراتي": "UAE",
            "القطري": "Qatar",
            "قطري": "Qatar",
            "الأردني": "Jordan",
            "أردني": "Jordan",
            "العراقي": "Iraq",
            "عراقي": "Iraq",
            "الكويتي": "Kuwait",
            "كويتي": "Kuwait",
            "العماني": "Oman",
            "عماني": "Oman",
            "البحريني": "Bahrain",
            "بحريني": "Bahrain",
            # English to Country
            "saudi": "Saudi Arabia",
            "egyptian": "Egypt",
            "egypt": "Egypt",
            "english": "England",
            "england": "England",
            "premier league": "England",
            "spanish": "Spain",
            "spain": "Spain",
            "la liga": "Spain",
            "italian": "Italy",
            "italy": "Italy",
            "serie a": "Italy",
            "french": "France",
            "france": "France",
            "ligue 1": "France",
            "german": "Germany",
            "germany": "Germany",
            "bundesliga": "Germany",
            "moroccan": "Morocco",
            "morocco": "Morocco",
            "algerian": "Algeria",
            "algeria": "Algeria",
            "tunisian": "Tunisia",
            "tunisia": "Tunisia",
            "uae": "UAE",
            "qatari": "Qatar",
            "qatar": "Qatar",
            "jordanian": "Jordan",
            "jordan": "Jordan",
            "iraqi": "Iraq",
            "iraq": "Iraq",
            "kuwaiti": "Kuwait",
            "kuwait": "Kuwait",
            "oman": "Oman",
            "bahrain": "Bahrain",
        }

        name_combined = (arabic_name + " " + english_name).lower()

        # Try to match country
        for key, country_name in country_mapping.items():
            if key in name_combined:
                try:
                    country, _ = Country.objects.get_or_create(
                        name=country_name, defaults={"code": country_name[:3].upper()}
                    )
                    return country
                except Exception as e:
                    logger.error(f"Error creating country {country_name}: {str(e)}")

        return None
