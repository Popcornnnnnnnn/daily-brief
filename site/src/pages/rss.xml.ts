import rss from '@astrojs/rss';
import type { APIContext } from 'astro';
import { marked } from 'marked';
import { getAllEditionsSorted } from '../lib/editions';

export async function GET(context: APIContext) {
  const editions = await getAllEditionsSorted();
  const site = context.site?.toString() || 'https://daily-brief.pages.dev';

  return rss({
    title: 'Daily Brief — AI, Security & Open-Source Developer Wire',
    description:
      'Daily AI, security, and open-source developer-tools briefing backed by commit-pinned inputs and verified primary sources.',
    site,
    items: editions.slice(0, 30).map((entry) => ({
      title: `Issue № ${String(entry.data.issue_number).padStart(3, '0')}: ${entry.data.title}`,
      pubDate: new Date(`${entry.data.date}T04:00:00+08:00`),
      description: entry.data.summary,
      link: `/issues/${entry.data.date}/`,
      categories: entry.data.tags,
      content: marked.parse(entry.body || '') as string,
    })),
    customData: `<language>en-us</language>`,
  });
}
