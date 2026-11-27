select id as run_id, tenant_id, trigger, state, score, score_max, partial, release_status, created_at, finished_at,
       extract(epoch from (finished_at - created_at)) as duration_s
from {{ source('app', 'runs') }}
