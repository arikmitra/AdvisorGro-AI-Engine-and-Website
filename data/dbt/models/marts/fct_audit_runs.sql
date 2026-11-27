select trigger, date_trunc('day', created_at) as day, count(*) as runs,
       count(*) filter (where partial) as partial_runs, count(*) filter (where state = 'InReview') as held_for_review,
       avg(duration_s) as avg_duration_s, avg(score) as avg_score
from {{ ref('stg_runs') }} group by 1, 2
