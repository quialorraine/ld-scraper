import asyncio
from playwright.async_api import async_playwright
import json
import argparse
import time
import re

class LinkedInScraper:
    """
    A class to scrape LinkedIn profile data, posts, comments, and reactions.
    """
    def __init__(self, cookie, profile_url):
        self.cookie = cookie
        self.profile_url = profile_url
        self.browser = None
        self.context = None
        # Extract user slug for later comment filtering
        match = re.search(r"/in/([^/]+)", self.profile_url)
        self.user_slug = match.group(1) if match else ""
        self.max_scroll_rounds = 30
        self.scroll_delay = 1.5
        # Limit for number of reactions to collect
        self.reactions_limit = 20

    async def initialize_browser(self):
        if self.browser is None:
            print("Launching browser...")
            p = await async_playwright().start()
            chromium_args = [
                '--disable-gpu',
                '--single-process',
                '--no-zygote',
                '--no-sandbox'
            ]
            self.browser = await p.chromium.launch(
                headless=True,
                   executable_path='/home/ruslan-nocode/.cache/ms-playwright/chromium-1117/chrome-linux/chrome',
                args=chromium_args
            )
            self.context = await self.browser.new_context()
            print("Setting cookie...")
            await self.context.add_cookies([{'name': 'li_at', 'value': self.cookie, 'domain': '.linkedin.com', 'path': '/'}])

    async def close_browser(self):
        if self.browser:
            print("Closing browser...")
            await self.browser.close()
            self.browser = None

    async def run(self, scrape_posts=False, scrape_comments=False, scrape_reactions=False):
        await self.initialize_browser()
        page = await self.context.new_page()
        
        scraped_data = {}
        try:
            print(f"Navigating to {self.profile_url} to scrape profile data...")
            await page.goto(self.profile_url, wait_until="domcontentloaded", timeout=60000)
            print("Navigation successful. Starting data extraction...")
            profile_data = await self.extract_profile_data(page)
            if profile_data:
                scraped_data.update(profile_data)

            # Extract full publications list
            full_pubs = await self.extract_full_publications(page)
            if full_pubs:
                scraped_data['publications'] = full_pubs

            if scrape_posts:
                activity_url = f"{self.profile_url.rstrip('/')}/recent-activity/all/"
                print(f"Navigating to posts page: {activity_url}")
                await page.goto(activity_url, wait_until="domcontentloaded", timeout=60000)
                posts = await self.extract_posts(page)
                if posts:
                    scraped_data['posts'] = posts

            if scrape_comments:
                comments_url = f"{self.profile_url.rstrip('/')}/recent-activity/comments/"
                print(f"Navigating to comments page: {comments_url}")
                await page.goto(comments_url, wait_until="domcontentloaded", timeout=60000)
                comments = await self.extract_comments_with_post_context(page)
                if comments:
                    scraped_data['comments'] = comments
            
            if scrape_reactions:
                reactions_url = f"{self.profile_url.rstrip('/')}/recent-activity/reactions/"
                print(f"Navigating to reactions page: {reactions_url}")
                await page.goto(reactions_url, wait_until="domcontentloaded", timeout=60000)
                reactions = await self.extract_reactions(page)
                if reactions:
                    scraped_data['reactions'] = reactions

        except Exception as e:
            print(f"An error occurred during navigation or extraction: {e}")
        
        return scraped_data

    async def extract_profile_data(self, page):
        """
        Extracts all the profile data from the page in a single pass.
        """
        print("Waiting for profile page to load...")
        await page.wait_for_selector('h1', timeout=60000)
        print("Profile page loaded.")

        profile = {}

        print("Extracting main profile data...")
        full_name = await page.locator('h1').first.inner_text()
        profile['first_name'] = full_name.split(' ')[0]
        profile['last_name'] = ' '.join(full_name.split(' ')[1:])
        profile['headline'] = await page.locator('div.text-body-medium').first.inner_text()
        
        try:
            profile['location'] = await page.locator('span.text-body-small.inline.t-black--light.break-words').first.inner_text()
            print("Location extracted.")
        except Exception:
            profile['location'] = "Not specified"
        
        print("Extracting About section...")
        try:
            about_section_locator = page.locator("section:has(h2:has-text('About'))")
            try:
                await about_section_locator.locator("button:has-text('See more')").click(timeout=2000)
            except Exception:
                pass
            
            full_text = await about_section_locator.inner_text()
            lines = [line.strip() for line in full_text.split('\n') if line.strip()]
            
            about_text_lines = []
            in_about_section = False
            for line in lines:
                if line.lower() == 'about' and not in_about_section:
                    in_about_section = True
                    continue
                if 'skills' in line.lower():
                    break
                if in_about_section:
                    about_text_lines.append(line)
            
            profile['about'] = ' '.join(about_text_lines)
        except Exception as e:
            print(f"Could not extract 'About' section text: {e}")
            profile['about'] = ''
        print("About section extracted.")

        print("Extracting Services section...")
        try:
            profile['services'] = await page.locator('section.artdeco-card:has-text("Services")').inner_text()
        except Exception:
            profile['services'] = "Not specified"
        print("Services section extracted.")
        
        profile['experience'] = await self.extract_experience(page)
        profile['publications'] = await self.extract_publications(page)
        profile['languages'] = await self.extract_languages(page)
        profile['education'] = await self.extract_education(page)
            
        return profile

    async def extract_experience(self, page):
        """
        Extracts the Experience section, correctly handling multi-line descriptions.
        """
        print("Extracting Experience section with improved description handling...")
        items = []
        try:
            experience_section = page.locator("section:has(h2:has-text('Experience'))")
            
            try:
                await experience_section.locator("button:has-text('Show all')").click(timeout=2000)
            except Exception:
                pass

            experience_items = await experience_section.locator("li.artdeco-list__item").all()

            for li in experience_items:
                if not await li.locator("span.t-14.t-normal.t-black--light").all():
                    continue

                try:
                    await li.locator("button.inline-show-more-text__button").click(timeout=1000)
                except Exception:
                    pass

                item = {}
                
                all_texts = await li.locator("span[aria-hidden='true']").all_inner_texts()
                all_texts = [text.strip() for text in all_texts if text.strip()]
                
                if not all_texts:
                    continue
                
                item['title'] = all_texts.pop(0)
                item['company'] = all_texts.pop(0) if all_texts else ''
                item['date'] = all_texts.pop(0) if all_texts else ''
                
                if all_texts and '·' not in all_texts[0] and 'yr' not in all_texts[0]:
                    item['location'] = all_texts.pop(0)
                else:
                    item['location'] = ''
                
                item['description'] = ' '.join(all_texts)
                
                items.append(item)
        except Exception as e:
            print(f"Could not extract Experience section: {e}")
            
        return items

    async def extract_publications(self, page):
        print("Extracting Publications section...")
        items = []
        try:
            publications_section = page.locator("section:has(h2:has-text('Publications'))")
            publication_items = await publications_section.locator("li.artdeco-list__item").all()
            for li in publication_items:
                item = {}
                full_text = await li.inner_text()
                lines = [line.strip() for line in full_text.split('\n') if line.strip()]
                if lines:
                    item['title'] = lines[0]
                    if len(lines) > 1:
                        item['publisher'] = lines[1]
                    if len(lines) > 2:
                        item['date'] = lines[2]
                    items.append(item)
        except Exception as e:
            print(f"Could not extract Publications section: {e}")
        return items

    async def extract_full_publications(self, page):
        """Navigate to /details/publications/ and collect the full list of publications"""
        print("Extracting full Publications list...")
        publications_url = f"{self.profile_url.rstrip('/')}/details/publications/"
        try:
            await page.goto(publications_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector("li.artdeco-list__item", timeout=20000)
            # Scroll to load all items
            last_height = 0
            scroll_attempts = 0
            while scroll_attempts < 10:
                await page.mouse.wheel(0, 5000)
                await page.wait_for_timeout(2000)
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    scroll_attempts += 1
                else:
                    last_height = new_height
                    scroll_attempts = 0
            pub_items = await page.locator("li.artdeco-list__item").all()
            items = []
            for li in pub_items:
                item = {}
                full_text = await li.inner_text()
                lines = [line.strip() for line in full_text.split('\n') if line.strip()]
                # Фильтруем блоки рекомендаций ("More profiles for you")
                if not lines:
                    continue
                if 'More profiles' in lines[0]:
                    continue
                # Часто в рекомендациях 3-я строка — «· 3rd»
                if len(lines) > 2 and lines[2].startswith('·'):
                    continue
                item['title'] = lines[0]
                if len(lines) > 1 and not lines[1].startswith('·'):
                    item['publisher'] = lines[1]
                if len(lines) > 2 and not lines[2].startswith('·'):
                    item['date'] = lines[2]
                items.append(item)
            print(f"Collected {len(items)} publications.")
            return items
        except Exception as e:
            print(f"Could not extract full Publications: {e}")
            return []

    async def extract_languages(self, page):
        """
        Extracts the Languages section, ensuring proficiency is parsed correctly by handling duplicate text.
        """
        print("Extracting Languages section with duplicate-handling logic...")
        items = []
        try:
            languages_section = page.locator("section:has(h2:has-text('Languages'))")
            language_elements = await languages_section.locator("li").all()

            for element in language_elements:
                item = {}
                full_text = await element.inner_text()
                lines = [line.strip() for line in full_text.split('\n') if line.strip()]
                
                distinct_lines = list(dict.fromkeys(lines))

                if distinct_lines:
                    item['language'] = distinct_lines[0]
                    if len(distinct_lines) > 1:
                        item['proficiency'] = distinct_lines[1]
                    else:
                        item['proficiency'] = 'Not specified'
                    
                    items.append(item)
        
        except Exception as e:
            print(f"Could not find or process the Languages section: {e}")
            
        return items

    async def extract_education(self, page):
        """
        Extracts the Education section, correctly handling multi-line descriptions.
        """
        print("Extracting Education section with improved description handling...")
        items = []
        try:
            education_section = page.locator("section:has(h2:has-text('Education'))")
            
            education_items = await education_section.locator("li.artdeco-list__item").all()

            for li in education_items:
                item = {}
                
                all_texts = await li.locator("span[aria-hidden='true']").all_inner_texts()
                all_texts = [text.strip() for text in all_texts if text.strip()]
                
                if not all_texts:
                    continue

                item['school'] = all_texts.pop(0)

                if all_texts:
                    item['degree'] = all_texts.pop(0)
                
                if all_texts:
                    item['date'] = all_texts.pop(0)
                    
                if all_texts:
                    item['description'] = ' '.join(all_texts)
                else:
                    item['description'] = ''

                items.append(item)

        except Exception as e:
            print(f"Could not find or process the Education section: {e}")
            
        return items

    async def extract_posts(self, page):
        print("Extracting posts...")
        posts_data = []
        try:
            await page.wait_for_selector("div[data-urn*='urn:li:activity:']", timeout=20000)
            print("Activity feed loaded.")
            last_height = 0
            scroll_attempts = 0
            while scroll_attempts < 10:
                await page.mouse.wheel(0, 5000)
                await page.wait_for_timeout(2000)
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    scroll_attempts += 1
                else:
                    last_height = new_height
                    scroll_attempts = 0
            
            post_cards = await page.locator("div[data-urn*='urn:li:activity:']").all()
            for card in post_cards:
                post_data = {}
                all_texts = await card.locator(".update-components-text").all()
                if all_texts:
                    post_data["user_commentary"] = (await all_texts[0].inner_text()).strip()
                    if len(all_texts) > 1:
                        post_data["re_post"] = "\n".join([(await t.inner_text()).strip() for t in all_texts[1:]])
                if post_data:
                    posts_data.append(post_data)
        except Exception as e:
            print(f"An error occurred while extracting posts: {e}")
        return posts_data

    async def extract_comments_with_post_context(self, page):
        """
        Scrolls through the user's comments activity feed and extracts every
        comment the user has left under other people's posts, together with
        the original post text, timestamp and post author.

        This version re-implements the logic from `linkedin_comment_parser.py`
        but adapted to Playwright's asynchronous API.
        """
        print("Extracting comments with full scrolling and JS evaluation...")
        comments_data = []
        try:
            await page.wait_for_selector("div.feed-shared-update-v2", timeout=20000)

            last_height = 0
            stagnant_rounds = 0
            for iteration in range(self.max_scroll_rounds):
                # Scroll to bottom to trigger lazy loading
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(int(self.scroll_delay * 1000))

                # Click explicit "Show more" button if it appears
                try:
                    load_more_button = page.locator("button.scaffold-finite-scroll__load-button").first
                    if await load_more_button.count() > 0 and await load_more_button.is_visible():
                        await load_more_button.click(timeout=5000)
                        await page.wait_for_timeout(int(self.scroll_delay * 1000))
                except Exception:
                    pass

                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    stagnant_rounds += 1
                else:
                    stagnant_rounds = 0

                if stagnant_rounds >= 2:
                    print("No new cards after multiple attempts – stopping scroll.")
                    break

                last_height = new_height
                print(f"Scroll round {iteration + 1}/{self.max_scroll_rounds} completed.")

            # After fully loaded, run JS extractor inside the page context
            extractor_js = '''
                (userSlug) => {
                    const COMMENT_CARD_SELECTOR = "div.feed-shared-update-v2";
                    const COMMENT_TEXT_SELECTOR = "span.comments-comment-item__main-content";
                    const POST_TEXT_SELECTOR = "div.update-components-text";
                    const TIMESTAMP_SELECTOR = "time";
                    const POST_AUTHOR_SELECTOR = "div.update-components-header a[href*='/company/'] , div.update-components-header a[href*='/in/']";

                    function isAuthoredByUser(commentEl) {
                        const container = commentEl.closest("li");
                        if (!container) return false;
                        const selfLink = Array.from(container.querySelectorAll("a[href*='/in/']"))
                            .find(a => a.getAttribute('href') && a.getAttribute('href').includes('/in/' + userSlug));
                        return !!selfLink;
                    }

                    const results = [];
                    document.querySelectorAll(COMMENT_CARD_SELECTOR).forEach(card => {
                        const postTextEl = card.querySelector(POST_TEXT_SELECTOR);
                        const timestampEl = card.querySelector(TIMESTAMP_SELECTOR);
                        const authorEl = card.querySelector(POST_AUTHOR_SELECTOR);

                        const commentEls = card.querySelectorAll(COMMENT_TEXT_SELECTOR);
                        commentEls.forEach(commentEl => {
                            if (!isAuthoredByUser(commentEl)) return;

                            const commentText = commentEl.innerText.trim();
                            if (!commentText) return;

                            results.push({
                                commentText,
                                postText: postTextEl ? postTextEl.innerText.trim() : null,
                                timestamp: timestampEl ? timestampEl.innerText.trim() : null,
                                postAuthor: authorEl ? authorEl.innerText.trim() : null,
                            });
                        });
                    });
                    return results;
                }
            '''
            comments_data = await page.evaluate(extractor_js, self.user_slug)
            print(f"Collected {len(comments_data)} comments.")
        except Exception as e:
            print(f"An error occurred while extracting comments: {e}")
        return comments_data

    async def extract_reactions(self, page):
        print("Extracting reactions (no scroll)...")
        reactions_data = []
        try:
            await page.wait_for_selector("div.feed-shared-update-v2", timeout=10000)
            print("Activity feed loaded for reactions.")
            
            # NO SCROLLING - process only initially visible posts
            post_cards = await page.locator("div.feed-shared-update-v2").all()
            print(f"Found {len(post_cards)} initial reaction cards.")
            
            # Take only up to self.reactions_limit cards
            post_cards = post_cards[:self.reactions_limit]
            
            for card in post_cards:
                post_data = {}
                actor_text_loc = card.locator(".feed-shared-actor__sub-description.t-12.t-normal.t-black--light").first
                if await actor_text_loc.count() > 0:
                    actor_text = await actor_text_loc.inner_text()
                    if "likes this" in actor_text or "celebrates this" in actor_text:
                        post_data["liked_by"] = actor_text.split(" ")[0]
                
                content_loc = card.locator(".update-components-text").first
                if await content_loc.count() > 0:
                    post_data["content"] = (await content_loc.inner_text()).strip()
                
                if post_data:
                    reactions_data.append(post_data)
        except Exception as e:
            print(f"An error occurred while extracting reactions: {e}")
        return reactions_data

async def run_and_save(args):
    profile_name = args.url.strip('/').split('/')[-1].split('?')[0]
    file_name = f'linkedin_profile_{profile_name}.json'
    
    existing_data = {}
    try:
        with open(file_name, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    except FileNotFoundError:
        pass

    scraper = LinkedInScraper(args.cookie, args.url)
    try:
        scraped_data = await scraper.run(
            scrape_posts=args.scrape_posts,
            scrape_comments=args.scrape_comments,
            scrape_reactions=args.scrape_reactions
        )
    finally:
        await scraper.close_browser()

    if scraped_data:
        existing_data.update(scraped_data)
        with open(file_name, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=4)
        print(f"\nScraping complete. Data saved to {file_name}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Scrape a LinkedIn profile and activities.")
    parser.add_argument("--cookie", required=True, help="LinkedIn session cookie (li_at).")
    parser.add_argument("--url", required=True, help="LinkedIn profile URL.")
    parser.add_argument("--scrape_posts", action='store_true', help="Scrape posts.")
    parser.add_argument("--scrape_comments", action='store_true', help="Scrape comments.")
    parser.add_argument("--scrape_reactions", action='store_true', help="Scrape reactions.")
    args = parser.parse_args()

    try:
        asyncio.run(run_and_save(args))
    except KeyboardInterrupt:
        print("\nScraping process cancelled by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
