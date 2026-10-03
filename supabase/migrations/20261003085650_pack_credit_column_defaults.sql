-- Restore server defaults and NOT NULL constraints required by the pack credit engine.
-- No data backfill or column type changes are included.

ALTER TABLE public.packcreditaccount
    ALTER COLUMN balance SET DEFAULT 0,
    ALTER COLUMN created_at SET DEFAULT now(),
    ALTER COLUMN updated_at SET DEFAULT now();

ALTER TABLE public.packcreditledger
    ALTER COLUMN currency SET DEFAULT 'AUD',
    ALTER COLUMN created_at SET DEFAULT now();

ALTER TABLE public.globalmonthlyusage
    ALTER COLUMN reserved_count SET DEFAULT 0,
    ALTER COLUMN completed_count SET DEFAULT 0,
    ALTER COLUMN updated_at SET DEFAULT now();

ALTER TABLE public.generationusage
    ALTER COLUMN status SET DEFAULT 'reserved',
    ALTER COLUMN credit_cost SET DEFAULT 1,
    ALTER COLUMN reserved_at SET DEFAULT now(),
    ALTER COLUMN usage_month SET DEFAULT date_trunc('month', timezone('UTC', now()))::date,
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN credit_cost SET NOT NULL,
    ALTER COLUMN reserved_at SET NOT NULL,
    ALTER COLUMN usage_month SET NOT NULL;
