select tenant_id, date_trunc('day', created_at) as day, count(*) as leads,
       count(*) filter (where first_contact_s <= 90) as within_sla, count(*) filter (where first_contact_at is null) as never_contacted
from {{ ref('stg_leads') }} group by 1, 2
