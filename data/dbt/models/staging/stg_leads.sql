-- deliberately omits name_enc, email_enc, phone_enc, email_hmac, phone_hmac
select id as lead_id, tenant_id, firm_id, campaign_id, state, created_at, first_contact_at,
       extract(epoch from (first_contact_at - created_at)) as first_contact_s
from {{ source('app', 'leads') }}
