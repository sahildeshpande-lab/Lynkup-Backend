from __future__ import annotations

from uuid import UUID, uuid4
from apps.profiles.db_models import Country, University

GB_ID = UUID("55555555-1111-1111-1111-000000000001")
IN_ID = UUID("55555555-1111-1111-1111-000000000002")
FR_ID = UUID("55555555-1111-1111-1111-000000000003")
UG_ID = UUID("55555555-1111-1111-1111-000000000004")
AE_ID = UUID("55555555-1111-1111-1111-000000000005")
BR_ID = UUID("55555555-1111-1111-1111-000000000006")
UA_ID = UUID("55555555-1111-1111-1111-000000000007")
CL_ID = UUID("55555555-1111-1111-1111-000000000008")
CN_ID = UUID("55555555-1111-1111-1111-000000000009")
US_ID = UUID("55555555-1111-1111-1111-000000000010")

SEED_COUNTRIES = [
    Country(id=GB_ID, name="United Kingdom", iso_code="GB"),
    Country(id=IN_ID, name="India", iso_code="IN"),
    Country(id=FR_ID, name="France", iso_code="FR"),
    Country(id=UG_ID, name="Uganda", iso_code="UG"),
    Country(id=AE_ID, name="United Arab Emirates", iso_code="AE"),
    Country(id=BR_ID, name="Brazil", iso_code="BR"),
    Country(id=UA_ID, name="Ukraine", iso_code="UA"),
    Country(id=CL_ID, name="Chile", iso_code="CL"),
    Country(id=CN_ID, name="China", iso_code="CN"),
    Country(id=US_ID, name="United States", iso_code="US"),
]

SEED_UNIVERSITIES = [
    University(
        id=uuid4(),
        name="West Herts College",
        slug="west-herts-college",
        country_id=GB_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Bhagwan Parshuram Institute of Technology",
        slug="bhagwan-parshuram-institute-of-technology",
        country_id=IN_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="National Institute of Applied Sciences of Toulouse",
        slug="national-institute-of-applied-sciences-of-toulouse",
        country_id=FR_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Bugema University",
        slug="bugema-university",
        country_id=UG_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Mohamed bin Zayed University of Artificial Intelligence (MBZUAI)",
        slug="mohamed-bin-zayed-university-of-artificial-intelligence-mbzuai",
        country_id=AE_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Centro Universitário de Brasília, UNICEUB",
        slug="centro-universitario-de-brasilia-uniceub",
        country_id=BR_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Kharkiv National University",
        slug="kharkiv-national-university",
        country_id=UA_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Universidad Técnica Federico Santa María",
        slug="universidad-tecnica-federico-santa-maria",
        country_id=CL_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="IÉSEG School of Management",
        slug="ieseg-school-of-management",
        country_id=FR_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Sun Yat-Sen University",
        slug="sun-yat-sen-university",
        country_id=CN_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Royal Holloway University of London",
        slug="royal-holloway-university-of-london",
        country_id=GB_ID,
        is_active=True,
    ),
    University(
        id=uuid4(),
        name="Marywood University",
        slug="marywood-university",
        country_id=US_ID,
        is_active=True,
    ),
]

SEED_USERS = []
SEED_PROFILES = []


def iter_seed_records() -> list:
    return [*SEED_COUNTRIES, *SEED_UNIVERSITIES, *SEED_USERS, *SEED_PROFILES]
