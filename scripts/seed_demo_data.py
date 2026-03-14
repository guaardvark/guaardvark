#!/usr/bin/env python3
"""
seed_demo_data.py - Populate Guaardvark with professional demo data.

Creates sample Clients, Projects, and Websites for product launch demos.
Idempotent: checks for existing data before inserting.

Usage:
    GUAARDVARK_ROOT=/path/to/guaardvark python scripts/seed_demo_data.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta

# Ensure project root is on the path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

os.environ.setdefault("GUAARDVARK_ROOT", project_root)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s"
)
logger = logging.getLogger("seed_demo_data")

try:
    from backend.app import app
    from backend.models import Client, Project, Website, db

    logger.info("Successfully imported backend modules.")
except ImportError as e:
    logger.error(f"Error importing backend modules: {e}", exc_info=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Demo data definitions
# ---------------------------------------------------------------------------

DEMO_CLIENTS = [
    {
        "name": "Meridian Technologies",
        "email": "hello@meridiantech.com",
        "phone": "(415) 555-0142",
        "description": "Enterprise software company specializing in cloud infrastructure, DevOps tooling, and scalable SaaS platforms for Fortune 500 clients.",
        "contact_url": "https://meridiantech.com/contact",
        "location": "San Francisco, CA",
        "industry": "Enterprise Software",
        "primary_service": "Cloud Infrastructure",
        "secondary_service": "DevOps Consulting",
        "brand_tone": "professional",
        "target_audience": "CTOs and VP Engineering at mid-to-large enterprises looking to modernize their cloud infrastructure and streamline deployment pipelines.",
        "unique_selling_points": "Zero-downtime migrations, 99.99% SLA guarantee, dedicated solutions architect for every account",
        "keywords": json.dumps(["cloud migration", "enterprise SaaS", "DevOps automation", "infrastructure as code", "CI/CD pipelines"]),
        "content_goals": "Establish thought leadership in cloud-native architecture. Drive enterprise demo requests through technical content marketing.",
        "geographic_coverage": "North America, EMEA",
    },
    {
        "name": "Bloom & Willow Creative",
        "email": "studio@bloomwillow.co",
        "phone": "(503) 555-0278",
        "description": "Award-winning design agency focused on brand identity, packaging design, and digital experiences for lifestyle and consumer brands.",
        "contact_url": "https://bloomwillow.co/hello",
        "location": "Portland, OR",
        "industry": "Creative Agency",
        "primary_service": "Brand Identity Design",
        "secondary_service": "Packaging Design",
        "brand_tone": "creative",
        "target_audience": "Founders and marketing directors at consumer brands (DTC, CPG, lifestyle) seeking premium brand identities that resonate with modern audiences.",
        "unique_selling_points": "Over 200 brands launched, 3x Webby Award winner, specialized in sustainable packaging design",
        "keywords": json.dumps(["brand identity", "packaging design", "creative agency", "visual storytelling", "DTC branding"]),
        "content_goals": "Showcase portfolio work and behind-the-scenes process. Attract premium clients through case studies and design thinking content.",
        "geographic_coverage": "United States",
    },
    {
        "name": "Summit Health Partners",
        "email": "info@summithealthpartners.org",
        "phone": "(720) 555-0391",
        "description": "Regional healthcare organization operating 12 clinics and a telehealth platform, serving over 200,000 patients across Colorado and Wyoming.",
        "contact_url": "https://summithealthpartners.org/contact",
        "location": "Denver, CO",
        "industry": "Healthcare",
        "primary_service": "Primary Care",
        "secondary_service": "Telehealth Services",
        "brand_tone": "professional",
        "target_audience": "Patients and families in the Colorado/Wyoming region seeking accessible, technology-forward primary and specialty care.",
        "unique_selling_points": "Same-day telehealth appointments, integrated patient portal, top-rated patient satisfaction scores in the region",
        "keywords": json.dumps(["healthcare", "telehealth", "patient portal", "primary care", "health technology"]),
        "content_goals": "Increase patient portal adoption and telehealth usage. Build trust through health education content and provider spotlights.",
        "regulatory_constraints": "HIPAA compliance required for all patient-facing content. Medical claims must be reviewed by clinical staff.",
        "geographic_coverage": "Colorado, Wyoming",
    },
    {
        "name": "Coastal Ventures Capital",
        "email": "partners@coastalventures.capital",
        "phone": "(305) 555-0517",
        "description": "Early-stage venture capital firm focused on climate tech, sustainable infrastructure, and ocean economy startups. $450M under management.",
        "contact_url": "https://coastalventures.capital/connect",
        "location": "Miami, FL",
        "industry": "Venture Capital",
        "primary_service": "Early-Stage Investment",
        "secondary_service": "Portfolio Advisory",
        "brand_tone": "professional",
        "target_audience": "Climate tech founders seeking Series A/B funding, and limited partners interested in impact-driven venture returns.",
        "unique_selling_points": "$450M AUM, 28 portfolio companies, dedicated climate tech thesis, operator-led investment team",
        "keywords": json.dumps(["venture capital", "climate tech", "sustainable investment", "impact investing", "ocean economy"]),
        "content_goals": "Attract top-tier climate tech deal flow. Publish quarterly market insights to establish thesis credibility with LPs and founders.",
        "geographic_coverage": "Global",
    },
    {
        "name": "Verde Sustainable Foods",
        "email": "contact@verdefoods.com",
        "phone": "(512) 555-0634",
        "description": "Organic food brand producing plant-based proteins and sustainably sourced snacks, sold in 4,000+ retail locations nationwide.",
        "contact_url": "https://verdefoods.com/contact",
        "location": "Austin, TX",
        "industry": "Food & Beverage",
        "primary_service": "Organic Food Products",
        "secondary_service": "Wholesale Distribution",
        "brand_tone": "friendly",
        "target_audience": "Health-conscious consumers aged 25-45 who prioritize organic, sustainable, and plant-based food options at mainstream retail price points.",
        "unique_selling_points": "100% certified organic, carbon-neutral supply chain, available in 4,000+ stores, B Corp certified",
        "keywords": json.dumps(["organic food", "plant-based protein", "sustainable snacks", "B Corp", "clean label"]),
        "content_goals": "Drive brand awareness and trial through recipe content, sustainability storytelling, and retail partnership announcements.",
        "geographic_coverage": "United States",
    },
    {
        "name": "Nexus Digital Media",
        "email": "team@nexusdigital.agency",
        "phone": "(212) 555-0789",
        "description": "Full-service digital marketing agency specializing in performance marketing, content strategy, and AI-powered analytics for growth-stage companies.",
        "contact_url": "https://nexusdigital.agency/contact",
        "location": "New York, NY",
        "industry": "Digital Marketing",
        "primary_service": "Performance Marketing",
        "secondary_service": "Content Strategy",
        "brand_tone": "bold",
        "target_audience": "Growth-stage SaaS and e-commerce companies ($5M-$100M ARR) seeking data-driven marketing that delivers measurable ROI.",
        "unique_selling_points": "Average 3.2x ROAS for clients, proprietary AI analytics platform, Google Premier Partner, Meta Business Partner",
        "keywords": json.dumps(["performance marketing", "digital agency", "AI analytics", "content strategy", "growth marketing"]),
        "content_goals": "Demonstrate ROI-driven results through case studies and data reports. Position as the go-to agency for AI-powered marketing.",
        "geographic_coverage": "United States, United Kingdom",
    },
]


def _projects_for(client_name):
    """Return project definitions keyed by client name."""
    now = datetime.now()

    projects_map = {
        "Meridian Technologies": [
            {
                "name": "Cloud Migration Phase 2",
                "description": "Second phase of enterprise cloud migration: containerizing legacy services, implementing Kubernetes orchestration, and establishing multi-region failover for all Tier-1 applications.",
                "project_type": "Infrastructure",
                "content_strategy": "Technical blog series documenting migration patterns and lessons learned, targeting DevOps decision-makers.",
                "deliverables": "Migration playbook, architecture diagrams, 6 technical blog posts, 2 case study videos",
                "target_keywords": json.dumps(["cloud migration", "Kubernetes", "multi-region", "zero downtime"]),
                "created_at": now - timedelta(days=90),
            },
            {
                "name": "Developer Portal Redesign",
                "description": "Complete redesign of the public-facing developer portal with improved API documentation, interactive code samples, and a new SDK onboarding experience.",
                "project_type": "Website Redesign",
                "content_strategy": "Developer-first content with code-heavy tutorials, API reference guides, and video walkthroughs.",
                "deliverables": "New developer portal, API reference docs, 12 tutorial articles, SDK quickstart guides",
                "target_keywords": json.dumps(["developer portal", "API documentation", "SDK", "developer experience"]),
                "created_at": now - timedelta(days=45),
            },
            {
                "name": "Enterprise Security Whitepaper Series",
                "description": "Quarterly whitepaper series covering SOC2 compliance, zero-trust architecture, and data sovereignty for enterprise prospects.",
                "project_type": "Content Campaign",
                "content_strategy": "Gated long-form content targeting CISOs and security teams, distributed via LinkedIn and industry events.",
                "deliverables": "4 whitepapers, executive summary one-pagers, LinkedIn ad creative, landing pages",
                "target_keywords": json.dumps(["enterprise security", "SOC2 compliance", "zero trust", "data sovereignty"]),
                "created_at": now - timedelta(days=20),
            },
        ],
        "Bloom & Willow Creative": [
            {
                "name": "Brand Refresh 2026",
                "description": "Comprehensive brand refresh including updated logo system, color palette expansion, typography overhaul, and new brand guidelines for digital and print applications.",
                "project_type": "Brand Identity",
                "content_strategy": "Behind-the-scenes content documenting the refresh process, culminating in a launch campaign across social and email.",
                "deliverables": "Brand guidelines, logo files, social media templates, email templates, business cards, letterhead",
                "target_keywords": json.dumps(["brand refresh", "visual identity", "brand guidelines", "design system"]),
                "created_at": now - timedelta(days=60),
            },
            {
                "name": "Sustainable Packaging Collection",
                "description": "Design and production of eco-friendly packaging for three new product lines using recycled materials and soy-based inks.",
                "project_type": "Packaging Design",
                "content_strategy": "Case study series highlighting sustainable design choices and environmental impact metrics.",
                "deliverables": "Packaging designs for 3 product lines, print-ready files, supplier specs, sustainability report",
                "target_keywords": json.dumps(["sustainable packaging", "eco-friendly design", "recycled materials"]),
                "created_at": now - timedelta(days=30),
            },
        ],
        "Summit Health Partners": [
            {
                "name": "Patient Portal Redesign",
                "description": "Full UX overhaul of the patient portal with improved appointment scheduling, telehealth integration, prescription management, and mobile-responsive design.",
                "project_type": "Website Redesign",
                "content_strategy": "Patient onboarding guides, video tutorials for new features, and email campaigns to drive adoption.",
                "deliverables": "Redesigned portal, mobile-responsive UI, onboarding flow, 8 tutorial videos, email drip sequence",
                "target_keywords": json.dumps(["patient portal", "telehealth", "healthcare UX", "appointment scheduling"]),
                "seo_strategy": "Local SEO targeting Colorado/Wyoming healthcare searches. Schema markup for medical practice listings.",
                "created_at": now - timedelta(days=120),
            },
            {
                "name": "Provider Recruitment Campaign",
                "description": "Multi-channel recruitment campaign to attract primary care physicians, nurse practitioners, and telehealth specialists to join the Summit Health network.",
                "project_type": "Content Campaign",
                "content_strategy": "Provider spotlight stories, culture content, and targeted ads on medical job boards and LinkedIn.",
                "deliverables": "Recruitment landing page, 6 provider spotlight articles, LinkedIn ad creative, job board postings",
                "target_keywords": json.dumps(["healthcare recruitment", "physician jobs Colorado", "telehealth careers"]),
                "created_at": now - timedelta(days=15),
            },
        ],
        "Coastal Ventures Capital": [
            {
                "name": "Investment Dashboard",
                "description": "Custom portfolio analytics dashboard for limited partners, featuring real-time fund performance, ESG impact metrics, and quarterly reporting automation.",
                "project_type": "Web Application",
                "content_strategy": "Internal tool with LP-facing quarterly reports auto-generated from dashboard data.",
                "deliverables": "Analytics dashboard, LP login portal, automated report generation, ESG scoring module",
                "target_keywords": json.dumps(["portfolio analytics", "LP dashboard", "ESG metrics", "fund performance"]),
                "created_at": now - timedelta(days=75),
            },
            {
                "name": "Climate Tech Market Report 2026",
                "description": "Comprehensive annual market report analyzing climate tech investment trends, emerging sectors, and portfolio company performance for public distribution.",
                "project_type": "Content Campaign",
                "content_strategy": "Flagship thought leadership piece distributed via email, social, events, and media partnerships.",
                "deliverables": "60-page market report, executive summary, infographic series, press release, social media assets",
                "target_keywords": json.dumps(["climate tech trends", "sustainable investment report", "ocean economy"]),
                "created_at": now - timedelta(days=40),
            },
        ],
        "Verde Sustainable Foods": [
            {
                "name": "Q1 Marketing Campaign",
                "description": "Integrated New Year marketing campaign promoting the new plant-based protein line across digital, social, retail, and influencer channels.",
                "project_type": "Marketing Campaign",
                "content_strategy": "Recipe-driven content with influencer partnerships, retail POS materials, and paid social targeting health-conscious consumers.",
                "deliverables": "Campaign creative, 20 recipe posts, influencer briefs, retail displays, social ad sets, email sequences",
                "target_keywords": json.dumps(["plant-based protein", "healthy recipes", "new year health", "organic snacks"]),
                "created_at": now - timedelta(days=50),
            },
            {
                "name": "E-Commerce Platform Launch",
                "description": "Launch of direct-to-consumer e-commerce platform with subscription boxes, recipe bundles, and a loyalty rewards program.",
                "project_type": "Website Launch",
                "content_strategy": "Launch campaign with email waitlist, social countdown, influencer unboxings, and a PR push.",
                "deliverables": "E-commerce site, subscription management, loyalty program, launch email sequence, PR kit",
                "target_keywords": json.dumps(["DTC food brand", "subscription box", "organic e-commerce", "loyalty program"]),
                "created_at": now - timedelta(days=10),
            },
        ],
        "Nexus Digital Media": [
            {
                "name": "AI Analytics Platform Beta",
                "description": "Beta launch of the proprietary AI-powered marketing analytics platform, featuring predictive campaign optimization and cross-channel attribution modeling.",
                "project_type": "Product Launch",
                "content_strategy": "Thought leadership on AI in marketing, beta user case studies, and product demo content.",
                "deliverables": "Beta platform, onboarding docs, 4 case studies, demo video, product landing page",
                "target_keywords": json.dumps(["AI marketing analytics", "predictive optimization", "attribution modeling"]),
                "created_at": now - timedelta(days=100),
            },
            {
                "name": "Agency Website Overhaul",
                "description": "Complete redesign of the agency website with updated case studies, interactive ROI calculator, and a new blog focused on data-driven marketing insights.",
                "project_type": "Website Redesign",
                "content_strategy": "Results-focused case studies with real metrics, interactive tools to engage prospects, and a weekly insights blog.",
                "deliverables": "New website, 10 case studies, ROI calculator tool, blog launch with 12 initial posts",
                "target_keywords": json.dumps(["digital marketing agency", "performance marketing results", "marketing ROI"]),
                "created_at": now - timedelta(days=25),
            },
        ],
    }

    return projects_map.get(client_name, [])


def _websites_for(client_name):
    """Return website definitions keyed by client name."""
    websites_map = {
        "Meridian Technologies": [
            {
                "url": "https://meridiantech.com",
                "sitemap": "https://meridiantech.com/sitemap.xml",
                "status": "crawled",
                "last_crawled": datetime.now() - timedelta(days=3),
            },
            {
                "url": "https://developers.meridiantech.io",
                "sitemap": "https://developers.meridiantech.io/sitemap.xml",
                "status": "crawled",
                "last_crawled": datetime.now() - timedelta(days=7),
            },
        ],
        "Bloom & Willow Creative": [
            {
                "url": "https://bloomwillow.co",
                "sitemap": "https://bloomwillow.co/sitemap.xml",
                "status": "crawled",
                "last_crawled": datetime.now() - timedelta(days=5),
            },
        ],
        "Summit Health Partners": [
            {
                "url": "https://summithealthpartners.org",
                "sitemap": "https://summithealthpartners.org/sitemap.xml",
                "status": "crawled",
                "last_crawled": datetime.now() - timedelta(days=2),
            },
            {
                "url": "https://portal.summithealthpartners.org",
                "status": "pending",
            },
        ],
        "Coastal Ventures Capital": [
            {
                "url": "https://coastalventures.capital",
                "sitemap": "https://coastalventures.capital/sitemap.xml",
                "status": "crawled",
                "last_crawled": datetime.now() - timedelta(days=10),
            },
        ],
        "Verde Sustainable Foods": [
            {
                "url": "https://verdefoods.com",
                "sitemap": "https://verdefoods.com/sitemap.xml",
                "status": "crawled",
                "last_crawled": datetime.now() - timedelta(days=1),
            },
            {
                "url": "https://shop.verdefoods.com",
                "status": "pending",
            },
        ],
        "Nexus Digital Media": [
            {
                "url": "https://nexusdigital.agency",
                "sitemap": "https://nexusdigital.agency/sitemap.xml",
                "status": "crawled",
                "last_crawled": datetime.now() - timedelta(days=4),
            },
        ],
    }

    return websites_map.get(client_name, [])


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------

def _fix_sequences():
    """
    Ensure id column defaults are linked to their sequences.
    Some migrations may have created sequences without binding them.
    """
    fixes = [
        ("clients", "clients_id_seq"),
        ("projects", "projects_id_seq"),
        ("websites", "websites_id_seq"),
    ]
    for table, seq in fixes:
        # Set default to use the sequence
        db.session.execute(
            db.text(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{seq}'::regclass)")
        )
        # Ensure the sequence is ahead of any existing rows
        db.session.execute(
            db.text(f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
        )
    db.session.commit()
    print("Fixed id sequences for clients, projects, websites.")


def seed_demo_data():
    """Insert demo clients, projects, and websites if they don't already exist."""
    _fix_sequences()

    created_clients = 0
    created_projects = 0
    created_websites = 0
    skipped_clients = 0

    for client_def in DEMO_CLIENTS:
        client_name = client_def["name"]

        # Check for existing client
        existing = Client.query.filter_by(name=client_name).first()
        if existing:
            print(f"  SKIP client '{client_name}' (already exists, id={existing.id})")
            skipped_clients += 1
            client = existing
        else:
            client = Client(**client_def)
            db.session.add(client)
            db.session.flush()  # get the id
            created_clients += 1
            print(f"  + Created client '{client_name}' (id={client.id})")

        # Projects for this client
        for proj_def in _projects_for(client_name):
            proj_name = proj_def["name"]
            existing_proj = Project.query.filter_by(name=proj_name).first()
            if existing_proj:
                print(f"    SKIP project '{proj_name}' (already exists)")
                continue

            proj = Project(client_id=client.id, **proj_def)
            db.session.add(proj)
            db.session.flush()
            created_projects += 1
            print(f"    + Created project '{proj_name}' (id={proj.id})")

            # Websites associated with client (link to first project if applicable)
            # We handle websites at the client level below

        # Websites for this client
        for i, site_def in enumerate(_websites_for(client_name)):
            site_url = site_def["url"]
            existing_site = Website.query.filter_by(url=site_url).first()
            if existing_site:
                print(f"    SKIP website '{site_url}' (already exists)")
                continue

            # Link to the first project for this client
            first_project = Project.query.filter_by(client_id=client.id).first()
            site = Website(
                client_id=client.id,
                project_id=first_project.id if first_project else None,
                **site_def,
            )
            db.session.add(site)
            created_websites += 1
            print(f"    + Created website '{site_url}'")

    db.session.commit()

    print("")
    print("=" * 60)
    print("  Demo data seeding complete!")
    print(f"  Clients:  {created_clients} created, {skipped_clients} skipped")
    print(f"  Projects: {created_projects} created")
    print(f"  Websites: {created_websites} created")
    print("=" * 60)


def main():
    print("Entering app context...")
    with app.app_context():
        print("Seeding Guaardvark demo data...")
        seed_demo_data()


if __name__ == "__main__":
    main()
