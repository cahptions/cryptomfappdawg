# Arbitrage source feasibility — 2026-10-22

The working desk separates active asking-price observations, dated operator scenarios, imported evidence and
inventory outcomes. It does not yet establish a profitable arbitrage edge or universal completed-sales coverage.
No runtime data or private account credentials were used in development.

| Source | What was actually read | Implemented use | Current limitation |
| --- | --- | --- | --- |
| [Amazon Product Fees API model](https://github.com/amzn/selling-partner-api-models/blob/main/models/product-fees-api-model/productFeesV0.json), Amazon, version v0 | Partial model: fee-estimate operations, descriptions, fee objects and rate-limit notes. Retrieved JSON is in `source_evidence/amazon_product_fees_model.txt`. | Dated manually reviewed channel costs; links and beginner guide in Settings. | No SP-API connector, seller authorization, current fee schedule or eligibility established. The model explicitly calls these estimates and says actual fees can differ. An estimate is not proof of sellability or API access. |
| [eBay OAuth Client Library README](https://github.com/eBay/ebay-oauth-python-client/blob/master/README.adoc), eBay, copyright 2019 | Full README, especially “Library Setup,” “Types of Tokens,” and “Grant Type: Client Credentials.” Local copy in `source_evidence/ebay_oauth_readme.txt`. | Separate production application credentials, application-token flow and read-only Browse searches. Token remains in memory. | This older README is evidence for the authentication mechanism, not evidence of present Browse access, quota, licensing, fees or free production approval. |
| [eBay Browse overview](https://developer.ebay.com/api-docs/buy/browse/overview.html) and [limits](https://developer.ebay.com/develop/get-started/api-call-limits) | Current documentation not accessible through this cloud network policy. A guessed GitHub Browse specification returned 404 and was **not** used as evidence. | Provisional implementation of production `item_summary/search`; synthetic OAuth/search, error, privacy and quota tests. Active fixed-price listings only; auctions skipped. | Must validate with authorized production access. Listings are asking prices, not completed sales. API availability, account approval and licensing must be checked before enabling. A local 2,000-attempt/day ceiling is not the provider quota. |
| [Adafruit product example](https://www.adafruit.com/product/4300) and [robots policy](https://www.adafruit.com/robots.txt) | Live page access blocked by cloud proxy. Not read as a live product or price observation. | Narrow fixed-host Product/Offer JSON-LD adapter, robots check, bounded responses and named errors. Real source URL seeded; no fake offer seeded. | Real markup compatibility and current access remain unverified. Redirects, absent/ambiguous variants, aggregate price ranges and forbidden robots paths fail explicitly. |
| [Shopify Ajax Product documentation](https://shopify.dev/docs/api/ajax/reference/product) | Blocked by cloud proxy; not read. | Not implemented. | Merchant permission, exposed variants, inventory semantics and allowed use need review before adding storefront hosts. |
| [Walmart developer portal](https://developer.walmart.com/) and [WFS](https://marketplace.walmart.com/walmart-fulfillment-services/) | Linked for operator setup; current documentation/account terms not read. | Manual reviewed Walmart/WFS scenarios and setup links only. | No seller API, eligibility, free data or flat fulfillment fee is assumed. |
| [Keepa API](https://keepa.com/#!api) | Linked, not read or subscribed. | Deferred guide only. | Outside the present $0 additional data budget; no claim of free or confirmed transaction history. |
| Own dated observations, sales and inventory records | Operator-supplied; no operator records accessed in this development pass. | Forms, machine-readable contracts, idempotent signal identifiers and append-only inventory events. | Provenance is an operator claim unless separately checked. Views, mentions and sold-listing counts never become confirmed market units. |

The local eBay README copy has trailing whitespace normalized; recorded byte counts describe the original retrieval.

The actual access results are recorded in `source_evidence/access*.json`. The first follow-up attempted inside
the shell sandbox could not connect even to GitHub; the subsequent allowed-host network run read the eBay README
successfully. This is distinguished from the retailer proxy denial and from a real retailer outage.

The cloud configuration draft was saved with additive domains `www.adafruit.com`, `developer.ebay.com`,
`api.ebay.com`, `shopify.dev`, `developer-docs.amazon.com`, `developer.walmart.com`, preserving previous custom
domains and package presets. The tool returned `status=saved, requires_publish=true`. This does **not** apply
the policy to the running machine or grant provider authorization. Review/save and publish in environment
settings are required for that cloud policy change. The operator laptop has its own network and is not altered.

Before treating a source as accepted: verify permitted access and retention; record a genuine present-day
observation and retrieval time; open the link and compare exact variant, price and declared stock; confirm errors
do not fabricate availability. Before accepting a purchase basket: independently check regional delivery,
quantity, exact destination match, seller documentation/eligibility, all fees, demand and cash-recovery timing.

eBay API cache and its corresponding scenario archive are conservatively capped at 24 hours pending fuller
licensing review. This cap is an implementation limit, **not** a legal conclusion that 24 hours is always allowed.
The explicit free-access confirmation includes checking permitted research use. Other retained price observations
are capped at 90 days/100,000 rows. No bulk historical source or actual sell-through history is fabricated.
