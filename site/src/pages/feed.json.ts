import type { APIContext } from 'astro';
import { marked } from 'marked';
import { getAllEditionsSorted } from '../lib/editions';

export async function GET(context: APIContext) {
  const editions = await getAllEditionsSorted();
  const base = (context.site?.toString() || 'https://daily-brief.pages.dev').replace(/\/$/, '');

  const feed = {
    version: 'https://jsonfeed.org/version/1.1',
    title: 'Daily Brief',
    home_page_url: `${base}/`,
    feed_url: `${base}/feed.json`,
    description:
      'Daily AI, security, and open-source developer-tools briefing backed by commit-pinned inputs and verified primary sources.',
    language: 'en-US',
    items: editions.slice(0, 30).map((entry) => ({
      id: `${base}/issues/${entry.data.date}/`,
      url: `${base}/issues/${entry.data.date}/`,
      title: `Issue № ${String(entry.data.issue_number).padStart(3, '0')}: ${entry.data.title}`,
      summary: entry.data.summary,
      content_text: entry.body || '',
      content_html: marked.parse(entry.body || '') as string,
      date_published: `${entry.data.date}T04:00:00+08:00`,
      tags: entry.data.tags,
      _daily_brief: {
        issue_number: entry.data.issue_number,
        window: entry.data.window,
        takeaways: entry.data.takeaways,
        story_count: entry.data.story_count,
        source_count: entry.data.source_count,
        provenance: entry.data.provenance ?? null,
        markdown_url: `${base}/editions/${entry.data.date}.md`,
      },
    })),
  };

  return new Response(JSON.stringify(feed, null, 2) + '\n', {
    headers: { 'Content-Type': 'application/feed+json; charset=utf-8' },
  });
}
