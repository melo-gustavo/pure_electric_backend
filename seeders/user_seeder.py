from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enums.user import DocumentType
from models.user import User
from utils.logger import get_logger
from utils.security import SecurityUtils

logger = get_logger("SEEDER")

SEED_USERS: list[dict] = [
    {
        "phone": "+5511999999991",
        "email": "maria.silva@example.com",
        "password": "Senha@123",
        "full_name": "Maria da Silva",
        "document_type": DocumentType.CPF,
        "cpf": "11111111111",
        "cnpj": None,
    },
    {
        "phone": "+5511999999992",
        "email": "contato@electric-strada.com.br",
        "password": "Senha@123",
        "full_name": "Auto Elétrica Strada Ltda",
        "document_type": DocumentType.CNPJ,
        "cpf": None,
        "cnpj": "11111111000111",
    },
    {
        "phone": "+5511999999993",
        "email": "ana.souza@example.com",
        "password": "Senha@123",
        "full_name": "Ana Souza",
        "document_type": DocumentType.CPF,
        "cpf": "22222222222",
        "cnpj": None,
    },
    {
        "phone": "+5511999999994",
        "email": "comercial@voltz-electric.com.br",
        "password": "Senha@123",
        "full_name": "Voltz Elétrica Industrial S.A.",
        "document_type": DocumentType.CNPJ,
        "cpf": None,
        "cnpj": "22222222000122",
    },
    {
        "phone": "+5511999999995",
        "email": "carla.mendes@example.com",
        "password": "Senha@123",
        "full_name": "Carla Mendes",
        "document_type": DocumentType.CPF,
        "cpf": "33333333333",
        "cnpj": None,
    },
]


async def seed_users(db: AsyncSession) -> None:
    """Create any missing seed users, leaving existing ones untouched."""
    created = 0
    for data in SEED_USERS:
        result = await db.execute(select(User).where(User.phone == data["phone"]))
        if result.scalar_one_or_none() is not None:
            continue

        values = dict(data)
        values["password"] = SecurityUtils.hash_password(values["password"])
        db.add(User(**values))
        created += 1

    await db.commit()
    logger.info("Seeded %d user(s)", created)
