import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const editions = defineCollection({
  loader: glob({ pattern: '????-??-??.md', base: '../editions' }),
  schema: z.object({
    issue_number: z.number(),
    date: z.string(),
    title: z.string(),
    window: z.string().default(''),
    summary: z.string().default(''),
    takeaways: z.array(z.string()).default([]),
    sections: z.array(z.string()).default([]),
    tags: z.array(z.string()).default([]),
    lead_image: z
      .object({
        src: z.string(),
        alt: z.string().default(''),
      })
      .nullable()
      .optional(),
    story_count: z.number().default(0),
    source_count: z.number().default(0),
    reading_time_min: z.number().default(5),
    provenance: z
      .object({
        candidates: z.number().nullable().optional(),
        unreviewed: z.number().nullable().optional(),
        revision: z.string().nullable().optional(),
        verified: z.boolean().optional(),
        coverage_complete: z.boolean().nullable().optional(),
        source_gap_count: z.number().optional(),
      })
      .nullable()
      .optional(),
  }),
});

export const collections = { editions };
