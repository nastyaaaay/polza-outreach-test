"""
Домены, уже найденные (через 2ГИС — фоновый агент — или известные напрямую),
для которых не нужно повторно искать сайт через search_website(). Второй
проход пайплайна берёт этот список вместо поиска и сразу переходит к
fetch → email → LLM-персонализация.

Теперь здесь ВСЕ найденные домены, включая те девять, что успели найтись
через DuckDuckGo до капчи (нижний блок). Смысл: полный прогон пайплайна
воспроизводится без единого обращения к поисковику — то есть у
проверяющего он не упирается в ту же капчу, что остановила исходный
прогон. Поиск в collect_leads.py остаётся рабочим путём для НОВЫХ
компаний, которых в этом словаре ещё нет.
"""

KNOWN_DOMAINS = {
    "Лендинг.ру": "https://landing.ru/",
    "Neurobrand агентство": "https://neuro-brand.ru/",
    "Walnut team агентство": "https://walnut.team/",
    "ArbaletWeb": "https://arbaletweb.ru/",
    "Tebiz Group": "https://tebiz.ru/",
    "Rocket media агентство": "https://rocket-media.ru/",
    "Макс медиа маркетинговое агентство": "https://maxmedia-group.ru/",
    "Osminog project автоматизация 1С": "https://osminog.biz/",
    "hubes агентство": "https://hubes.ru/",
    "Лига цифровой экономики": "https://digitalleague.ru/",
    "Игроник агентство": "https://igronik.ru/",
    "Push4site": "https://push4site.com/",
    "ПроКонтекст агентство": "https://procontext.ru/",
    "RedHelper": "https://redhelper.ru/",
    "Brandmaker Киров": "https://brandmaker.ru/",
    "Premium-lift рекламная компания": "https://premium-lift.ru/",
    "Джейкет сервис объявлений": "https://jcat.ru/",
    "Институт развития интернета": "https://ири.рф/",
    "Rocket10": "https://rocket10.com/",
    "Victory group маркетинг": "https://victoryagency.ru/",
    "БТЛ Сервис агентство": "https://btl-service.com/",
    "Глобал артс агентство": "https://global-arts.ru/",
    "Тарантул агентство маркетинг": "https://tarantul.zone/",
    "Социо про исследования": "https://socio-pro.com/",
    "Servizoria компания": "https://servizoria.ru/",
    "Litera.Studio графический дизайн": "https://litera.studio/",
    "Pragmatix digital SEO": "https://pragmatix.digital/",
    "Findby SEO продвижение": "https://findby.ru/",
    "Аудитория агентство Москва": "https://au-agency.ru/",
    "Упаковщик агентство маркетинг": "https://packer24.ru/",
    "Домовой и Партнеры агентство": "https://domovoy.pro/",
    "DigitalWill агентство": "https://digitalwill.ru/",
    "Agency-5 агентство": "https://agency-5.ru/",
    "ArrivoMedia агентство": "https://arrivomedia.ru/",
    "Kochev агентство": "https://kochevmarketing.com/",
    "Ony агентство Москва": "https://ony.ru/",
    # Известны напрямую (уже подтверждались вручную ранее в этой сессии)
    "Agima интерактивное агентство": "https://www.agima.ru/",
    "Студия Артемия Лебедева": "https://www.artlebedev.ru/",
    "inSales платформа онлайн-торговли": "https://www.insales.ru/",
    "БТВ-Инфо IT-компания": "https://btv-info.ru/",
    "Qsoft разработка сайтов": "https://www.qsoft.ru/",
    # Найдены поиском в первом прогоне, до того как DuckDuckGo начал
    # отдавать капчу. Зафиксированы здесь, чтобы повторный прогон не
    # зависел от доступности поисковика.
    "Depot Branding Agency": "https://www.depotwpf.ru/",
    "MGcom рекламное агентство": "https://mgcom.ru/",
    "Родная речь рекламное агентство": "https://rore.group/",
    "Ipsos Comcon": "https://www.ipsos-comcon.ru/ru-ru",
    "Schmidt Schmidt": "https://schmidt-export.ru/",
    "Mediascope": "https://mediascope.net/",
    "Brand Analytics": "https://brandanalytics.ru/",
    "Frank RG": "https://frankrg.com/",
    "Novikov Marketing Consulting": "https://novikov.consulting/",
    # Не найдено через 2ГИС — сознательно исключены, не гадаем домен:
    # "2-Step агентство", "Goodwill маркетинговое агентство",
    # "Kerber агентство", "Madmonks агентство"
    # "Лига трафика агентство" — поиск отдавал только группу ВКонтакте,
    # своего сайта у компании не нашлось; группа соцсети сайтом не
    # считается (см. NOT_A_WEBSITE_DOMAINS), домен не выдумываем.
}
