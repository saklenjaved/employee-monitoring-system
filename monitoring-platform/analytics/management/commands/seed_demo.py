"""Fill a local database with demo organizations, people, devices and 30 days of activity.

Running it again changes nothing: every row is looked up by its natural key first, and the
activity numbers come from a fixed seed per person and day. Local development only.

    python manage.py seed_demo

All demo accounts share one password, read from DEMO_PASSWORD (see .env.example).
"""
import hashlib
import os
import random
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Membership, MembershipStatus, Role, User
from analytics.models import ActivityDaily, ItemUsageDaily
from devices.models import Device
from monitoring.models import MonitoringPolicy
from organizations.models import Organization
from productivity.models import ClassificationRule
from workforce.models import Department, EmployeeProfile, Team, UserTeam

DAYS = 30
DEFAULT_PASSWORD = "demo-password-123"

RULES = [
    ("app", "code.exe", "productive"),
    ("app", "excel.exe", "productive"),
    ("app", "winword.exe", "productive"),
    ("app", "outlook.exe", "neutral"),
    ("app", "slack.exe", "neutral"),
    ("app", "chrome.exe", "neutral"),
    ("app", "spotify.exe", "unproductive"),
    ("app", "steam.exe", "unproductive"),
    ("website", "github.com", "productive"),
    ("website", "stackoverflow.com", "productive"),
    ("website", "docs.google.com", "productive"),
    ("website", "mail.google.com", "neutral"),
    ("website", "youtube.com", "unproductive"),
    ("website", "facebook.com", "unproductive"),
]
# Weight of each item in a typical day. Items without a rule show up as unclassified.
APPS = {
    "code.exe": 5, "excel.exe": 3, "winword.exe": 2, "outlook.exe": 3, "slack.exe": 3,
    "chrome.exe": 4, "spotify.exe": 1, "steam.exe": 0.4, "figma.exe": 1.2, "teams.exe": 1.5,
}
SITES = {
    "github.com": 3, "stackoverflow.com": 2, "docs.google.com": 2, "mail.google.com": 2,
    "youtube.com": 1, "facebook.com": 0.6, "notion.so": 1.2, "linkedin.com": 0.8,
}

ORGANIZATIONS = [
    {
        "name": "Northwind Analytics",
        "slug": "northwind",
        "timezone": "UTC",
        "departments": {"Engineering": ["Platform", "Data"], "Operations": ["Support"]},
        "managers": [("Amara Okafor", "Platform"), ("Lena Fischer", "Data"), ("Diego Ramos", "Support")],
        "employees": [
            ("Priya Nair", "Platform"), ("Tom Becker", "Platform"), ("Yuki Tanaka", "Platform"),
            ("Sara Haddad", "Data"), ("Omar Farouk", "Data"), ("Mia Kowalski", "Data"),
            ("Ken Adeyemi", "Support"), ("Elif Demir", "Support"), ("Noah Silva", "Support"),
            ("Chloe Martin", "Platform"),
        ],
    },
    {
        "name": "Contoso Logistics",
        "slug": "contoso",
        "timezone": "Europe/London",
        "departments": {"Dispatch": ["Routing"], "Finance": ["Billing"]},
        "managers": [("Hannah Wright", "Routing"), ("Ravi Patel", "Billing")],
        "employees": [
            ("Jonas Berg", "Routing"), ("Aisha Bello", "Routing"), ("Leo Rossi", "Routing"),
            ("Grace Kim", "Billing"), ("Ivan Petrov", "Billing"), ("Zara Ahmed", "Billing"),
        ],
    },
]


# Demo people for an existing organization (--into). Emails end in .demo so they are easy to find and remove.
YORK_DEMO = {
    "name": "",
    "slug": "york",
    "timezone": "Asia/Kolkata",
    "departments": {"Engineering": ["Platform", "Data"], "Operations": ["Support", "Sales"]},
    "managers": [("Asha Mehta", "Platform"), ("Ravi Patel", "Data"), ("Neha Sharma", "Support")],
    "employees": [
        ("Arjun Rao", "Platform"), ("Meera Iyer", "Platform"), ("Kiran Shah", "Platform"),
        ("Sana Khan", "Data"), ("Vikram Singh", "Data"),
        ("Pooja Gupta", "Support"), ("Rahul Verma", "Support"),
        ("Anita Desai", "Sales"), ("Sameer Joshi", "Sales"), ("Divya Menon", "Sales"),
    ],
}


def _email(full_name, slug):
    return f"{full_name.lower().replace(' ', '.')}@{slug}.demo"


class Command(BaseCommand):
    help = "Create demo organizations, users of each role, devices and 30 days of activity (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--into",
            metavar="ADMIN_EMAIL",
            help="Add demo teams and people to the organization of this existing admin. Its rules and admin stay as they are.",
        )

    def handle(self, *args, **options):
        if not (settings.DEBUG or os.environ.get("DEMO_SEED_ALLOWED") == "1"):
            raise CommandError("seed_demo is for local development. Set DJANGO_DEBUG=1 (or DEMO_SEED_ALLOWED=1).")
        self.password = os.environ.get("DEMO_PASSWORD", DEFAULT_PASSWORD)
        self.created = 0
        if options["into"]:
            admin = User.objects.filter(email=options["into"], role=Role.ADMIN).select_related("organization").first()
            if admin is None or admin.organization is None:
                raise CommandError(f"No admin with an organization found for {options['into']}.")
            with transaction.atomic():
                self._organization(YORK_DEMO, org=admin.organization, seed_rules=False)
            self.stdout.write(self.style.SUCCESS(f"Demo people added to {admin.organization.name} ({self.created} new accounts)."))
            self.stdout.write("Demo emails end in .demo. Their devices are fake and cannot connect to the agent API.")
            return
        with transaction.atomic():
            self._user("super@platform.demo", "Platform Owner", Role.SUPER_ADMIN, None)
            for spec in ORGANIZATIONS:
                self._organization(spec)
        self.stdout.write(self.style.SUCCESS(f"Demo data ready ({self.created} new accounts)."))
        self.stdout.write("Sign in with any demo email, for example admin@northwind.demo, using DEMO_PASSWORD.")

    # --- People -------------------------------------------------------------------------

    def _user(self, email, full_name, role, organization):
        user = User.objects.filter(email=email).first()
        if user is None:
            if role == Role.SUPER_ADMIN:
                user = User.objects.create_superuser(email=email, password=self.password, full_name=full_name)
            else:
                user = User.objects.create_user(
                    email=email, password=self.password, full_name=full_name, role=role, organization=organization
                )
            self.created += 1
        if role != Role.SUPER_ADMIN:
            Membership.objects.get_or_create(
                user=user,
                organization=organization,
                status=MembershipStatus.ACTIVE,
                defaults={"role": role, "activated_at": timezone.now()},
            )
        return user

    def _organization(self, spec, org=None, seed_rules=True):
        if org is None:
            org, _ = Organization.objects.get_or_create(name=spec["name"])
            MonitoringPolicy.objects.get_or_create(organization=org, defaults={"timezone": spec["timezone"]})
            self._user(f"admin@{spec['slug']}.demo", f"{spec['name']} Admin", Role.ADMIN, org)

        teams = {}
        for dept_name, team_names in spec["departments"].items():
            department, _ = Department.objects.get_or_create(organization=org, name=dept_name)
            for team_name in team_names:
                teams[team_name], _ = Team.objects.get_or_create(
                    organization=org, name=team_name, defaults={"department": department}
                )

        for item_type, pattern, category in (RULES if seed_rules else []):
            ClassificationRule.objects.get_or_create(
                organization=org, item_type=item_type, pattern=pattern, team=None, defaults={"category": category}
            )

        people = [(n, t, Role.MANAGER) for n, t in spec["managers"]] + [(n, t, Role.EMPLOYEE) for n, t in spec["employees"]]
        for index, (full_name, team_name, role) in enumerate(people, start=1):
            user = self._user(_email(full_name, spec["slug"]), full_name, role, org)
            team = teams[team_name]
            UserTeam.objects.get_or_create(organization=org, team=team, user=user)
            EmployeeProfile.objects.get_or_create(
                user=user,
                organization=org,
                defaults={"department": team.department, "employee_code": f"{spec['slug'][:2].upper()}-{index:03d}"},
            )
            if role == Role.EMPLOYEE:
                self._device(org, user)
                self._activity(org, user, spec["timezone"])

    def _device(self, org, user):
        rng = random.Random(f"device:{user.email}")
        name = f"{user.full_name.split()[0].upper()}-LAPTOP"
        device = Device.objects.filter(organization=org, user=user, name=name).first()
        if device is None:
            device = Device.objects.create(
                organization=org,
                user=user,
                membership=Membership.objects.filter(user=user, status=MembershipStatus.ACTIVE).first(),
                name=name,
                os_version="Windows 11 23H2",
                agent_version="0.1.0",
                architecture="x64",
                # A random secret nobody knows: demo devices cannot call the agent API.
                credential_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                credential_expires_at=timezone.now() + timedelta(days=365),
                session_type="console",
                monitoring_state="monitoring",
            )
        # About three in four demo devices look online on every run.
        minutes_ago = rng.choice([1, 2, 4, 6, 9, 3, 60 * 5])
        device.last_seen_at = timezone.now() - timedelta(minutes=minutes_ago)
        device.last_successful_sync_at = device.last_seen_at
        device.save(update_fields=["last_seen_at", "last_successful_sync_at"])

    # --- Activity -----------------------------------------------------------------------

    def _activity(self, org, user, tz_name):
        from zoneinfo import ZoneInfo

        today = timezone.now().astimezone(ZoneInfo(tz_name)).date()
        focus = random.Random(f"focus:{user.email}").uniform(0.75, 1.25)  # some people work more focused
        for offset in range(DAYS):
            day = today - timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            rng = random.Random(f"{user.email}:{day.isoformat()}")
            if rng.random() < 0.05:
                continue  # an occasional day off
            worked = rng.uniform(6.0, 8.5) * 3600
            if offset == 0:
                worked *= 0.6  # today is still in progress
            idle_share = min(0.35, max(0.05, rng.gauss(0.16, 0.05) / focus))
            ActivityDaily.objects.update_or_create(
                organization=org, user=user, date=day,
                defaults={"active_seconds": round(worked * (1 - idle_share)), "idle_seconds": round(worked * idle_share)},
            )
            self._items(org, user, day, "app", APPS, worked, rng, focus)
            self._items(org, user, day, "website", SITES, worked * 0.45, rng, focus)

    @staticmethod
    def _items(org, user, day, item_type, weights, total, rng, focus):
        unproductive = {"spotify.exe", "steam.exe", "youtube.com", "facebook.com"}
        scaled = {
            name: weight * rng.uniform(0.4, 1.6) * (1 / focus if name in unproductive else 1)
            for name, weight in weights.items()
        }
        weight_sum = sum(scaled.values())
        for name, weight in scaled.items():
            ItemUsageDaily.objects.update_or_create(
                organization=org, user=user, date=day, item_type=item_type, name=name,
                defaults={"seconds": round(total * weight / weight_sum)},
            )
