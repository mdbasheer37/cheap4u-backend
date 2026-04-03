import json
from models import db, DataPlan, CablePlan, ElectricityProvider

def init_data_plans():
    # Data plans from your table
    data_plans = [
        # Format: (plan_id, provider, size, duration, selling_price, cost_price)
        (70, "airtel", "1GB (Social Bundle)", "3 Days", 350.0, 295.0),
        (13, "airtel", "500MB", "7 days", 500.0, 490.0),
        (69, "airtel", "1.5GB", "1 Day", 530.0, 500.0),
        (66, "airtel", "1.5GB", "2 Days", 650.0, 599.0),
        (15, "airtel", "1GB", "7 Days", 800.0, 785.0),
        (17, "airtel", "2GB", "30 Days", 1500.0, 1470.0),
        (52, "airtel", "5GB", "7 Days", 1599.0, 1570.0),
        (18, "airtel", "3GB", "30 Days", 2100.0, 1960.0),
        (22, "airtel", "6GB", "7 Days", 2599.0, 2455.0),
        (19, "airtel", "4GB", "30 Days", 2650.0, 2570.0),
        (20, "airtel", "8GB", "30 Days", 3200.0, 2999.0),
        (21, "airtel", "10GB", "30 Days", 4200.0, 4070.0),
        (42, "glo", "200 MB", "1 Day", 100.0, 89.0),
        (35, "glo", "500MB", "30 Days", 250.0, 225.0),
        (68, "glo", "1GB", "3 Days", 330.0, 280.0),
        (36, "glo", "1GB", "30 Days", 450.0, 425.0),
        (41, "glo", "1GB", "14 Days", 500.0, 485.0),
        (40, "glo", "2GB", "30 Days", 900.0, 840.0),
        (37, "glo", "3GB", "30 Days", 1400.0, 1290.0),
        (54, "glo", "5GB", "7 Days", 1800.0, 1690.0),
        (38, "glo", "5GB", "30 Days", 2250.0, 2190.0),
        (39, "glo", "10GB", "30 Days", 4500.0, 4390.0),
        (59, "glo", "20.5GB", "30 Days", 6000.0, 5300.0),
        (58, "glo", "107GB", "30 Days", 20000.0, 19300.0),
        (43, "mtn", "110MB", "1 Day", 100.0, 99.0),
        (74, "mtn", "230MB", "1 Day", 250.0, 200.0),
        (44, "mtn", "500MB", "30 Days", 400.0, 350.0),
        (45, "mtn", "1GB", "7 Days", 499.0, 450.0),
        (46, "mtn", "1GB", "30 Days", 600.0, 570.0),
        (47, "mtn", "2GB", "7 Days", 950.0, 930.0),
        (60, "mtn", "3.5GB", "1 Day", 1000.0, 980.0),
        (27, "mtn", "2.5GB", "2 Days", 1000.0, 900.0),
        (71, "mtn", "2GB", "7 Days", 1000.0, 900.0),
        (48, "mtn", "2GB", "30 Days", 1250.0, 1150.0),
        (61, "mtn", "4GB", "2 Days", 1300.0, 1175.0),
        (49, "mtn", "3GB", "30 Days", 1500.0, 1370.0),
        (50, "mtn", "5GB", "30 Days", 2300.0, 2050.0),
        (53, "mtn", "6GB", "7 Days", 2600.0, 2495.0),
        (55, "mtn", "11GB", "7 Days", 3450.0, 3430.0),
        (33, "mtn", "7GB", "30 Days", 3599.0, 3499.0),
        (67, "mtn", "10GB", "30 Days", 5000.0, 4470.0),
        (57, "mtn", "36GB", "30 Days", 11000.0, 10800.0),
        (51, "mtn", "75GB", "30 Days", 18500.0, 17990.0),
    ]
    for plan in data_plans:
        existing = DataPlan.query.filter_by(plan_id=plan[0]).first()
        if not existing:
            db.session.add(DataPlan(
                plan_id=plan[0],
                provider=plan[1],
                size=plan[2],
                duration=plan[3],
                selling_price=plan[4],
                cost_price=plan[5]
            ))
    db.session.commit()
    print("✅ Data plans inserted")

def init_cable_plans():
    # Cable plans from your table (I've added estimated cost prices as 95% of selling price – adjust if you have real costs)
    cable_plans = [
        (3, "DSTV", "DStv Padi", 4400.0, 4180.0),
        (4, "GOTV", "GOtv Smallie-monthly", 1900.0, 1805.0),
        (5, "STARTIMES", "Nova (antenna) -1 week", 700.0, 665.0),
        (6, "DSTV", "DStv Yanga", 6000.0, 5700.0),
        (7, "DSTV", "DStv Confam", 11000.0, 10450.0),
        (8, "DSTV", "DStv Compact", 19000.0, 18050.0),
        (9, "DSTV", "DStv Compact Plus", 30000.0, 28500.0),
        (10, "DSTV", "DStv Premium", 44500.0, 42275.0),
        (11, "GOTV", "GOtv Jinja", 3900.0, 3705.0),
        (12, "GOTV", "Gotv Jolli", 5800.0, 5510.0),
        (13, "GOTV", "GOtv Max", 8500.0, 8075.0),
        (14, "GOTV", "GOtv Supa", 11400.0, 10830.0),
        (15, "GOTV", "GOtv Supa Plus", 16800.0, 15960.0),
        (16, "STARTIMES", "Nova (Dish) - 1 Week", 700.0, 665.0),
        (17, "STARTIMES", "Nova (Antenna) - 1 Month", 2100.0, 1995.0),
        (18, "STARTIMES", "Basic (Antenna) -1 Week", 1400.0, 1330.0),
        (19, "STARTIMES", "Basic (Dish) - 1 week", 1700.0, 1615.0),
        (20, "STARTIMES", "Basic (Antenna)- 1 month", 4000.0, 3800.0),
        (21, "STARTIMES", "Basic (dish) - 1Month", 5100.0, 4845.0),
        (22, "STARTIMES", "Classic (Dish) - 1 Week", 2500.0, 2375.0),
        (23, "STARTIMES", "Classic (Dish) -1 Month", 7400.0, 7030.0),
        (24, "STARTIMES", "Super (Dish) - 1 Week", 3300.0, 3135.0),
        (25, "STARTIMES", "Super (Antenna) - 1 week", 3200.0, 3040.0),
        (26, "STARTIMES", "Super (Antenna) -1 Month", 9500.0, 9025.0),
    ]
    for plan in cable_plans:
        existing = CablePlan.query.filter_by(plan_id=plan[0]).first()
        if not existing:
            db.session.add(CablePlan(
                plan_id=plan[0],
                provider=plan[1],
                plan_name=plan[2],
                selling_price=plan[3],
                cost_price=plan[4]
            ))
    db.session.commit()
    print("✅ Cable plans inserted")

def init_electricity_providers():
    providers = [
        (1, "Abuja Electric AEDC", 0.0),
        (2, "Eko Electric (EKEDC)", 0.0),
        (3, "Ibadan Electric (IBEDC)", 0.0),
        (4, "Ikeja Electric (IKEDC)", 0.0),
        (5, "Kaduna Electric", 0.0),
        (6, "Port Harcourt Electric", 0.0),
        (7, "Jos Electricity Distribution PLC (JEDplc)", 0.0),
        (8, "Enugu Electric", 0.0),
        (9, "Yola Electric", 0.0),
        (10, "Benin Electric", 0.0),
    ]
    for prov in providers:
        existing = ElectricityProvider.query.filter_by(provider_id=prov[0]).first()
        if not existing:
            db.session.add(ElectricityProvider(
                provider_id=prov[0],
                name=prov[1],
                discount_percent=prov[2]
            ))
    db.session.commit()
    print("✅ Electricity providers inserted")

def init_all():
    init_data_plans()
    init_cable_plans()
    init_electricity_providers()
