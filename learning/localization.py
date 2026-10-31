"""Offline French and Arabic translations for the reviewed question bank."""
from copy import deepcopy

from .questions import BANK, SUBJECTS


SUBJECT_TRANSLATIONS = {
    'fr': {
        'ml': 'Apprentissage automatique',
        'cyber': 'Cybersécurité',
        'ai': 'Intelligence artificielle',
    },
    'ar': {
        'ml': 'تعلّم الآلة',
        'cyber': 'الأمن السيبراني',
        'ai': 'الذكاء الاصطناعي',
    },
}

TOPIC_TRANSLATIONS = {
    'fr': {
        'Foundations': 'Fondamentaux',
        'Evaluation': 'Évaluation',
        'Generalization': 'Généralisation',
        'Access control': 'Contrôle d’accès',
        'Data protection': 'Protection des données',
        'Secure development': 'Développement sécurisé',
        'Search': 'Recherche',
        'Learning systems': 'Systèmes d’apprentissage',
        'AI verification': 'Vérification de l’IA',
    },
    'ar': {
        'Foundations': 'الأساسيات',
        'Evaluation': 'التقييم',
        'Generalization': 'التعميم',
        'Access control': 'التحكم في الوصول',
        'Data protection': 'حماية البيانات',
        'Secure development': 'التطوير الآمن',
        'Search': 'البحث',
        'Learning systems': 'أنظمة التعلّم',
        'AI verification': 'التحقق من الذكاء الاصطناعي',
    },
}


# Each item follows question-bank order: topic, prompt, choices, explanation, hint.
QUESTION_TEXT = {
    'fr': {
        'ml': [
            ('Fondamentaux', 'Prédire le prix de vente d’une maison correspond généralement à quel type de tâche ?', ('Classification', 'Régression', 'Regroupement', 'Extraction d’associations'), 'Le prix est une valeur numérique à prédire : il s’agit donc d’une régression.', 'La cible est-elle une catégorie ou une valeur numérique ?'),
            ('Fondamentaux', 'Quelle méthode regroupe des exemples similaires sans utiliser d’étiquettes cibles ?', ('Classification supervisée', 'Régression linéaire', 'Regroupement', 'Classement supervisé'), 'Le regroupement recherche des groupes dans des données non étiquetées.', 'Cherchez une méthode non supervisée.'),
            ('Évaluation', 'Un classificateur signale 24 courriels comme hameçonnage. Parmi eux, 18 en sont réellement. Quelle est sa précision ?', ('25 %', '50 %', '75 %', '90 %'), 'Précision = vrais positifs / prédictions positives = 18 / 24 = 75 %.', 'Divisez les prédictions positives correctes par toutes les prédictions positives.'),
            ('Évaluation', 'Quel jeu de données faut-il réserver à l’évaluation finale après le choix du modèle ?', ('Entraînement', 'Test', 'Copie du jeu d’entraînement', 'Classe la plus grande uniquement'), 'Un jeu de test intact estime les performances après le choix du modèle.', 'Il ne doit pas influencer l’entraînement ni le choix du modèle.'),
            ('Généralisation', 'La précision d’entraînement est de 99 % et celle de validation de 65 %. Quel problème est plausible ?', ('Le modèle est forcément sans biais', 'Surapprentissage', 'Généralisation parfaite', 'Précision d’entraînement trop faible'), 'Un grand écart suggère un surapprentissage, même si d’autres causes sont possibles.', 'Comparez les performances sur les exemples vus et non vus.'),
            ('Généralisation', 'Une IA propose d’ajuster un normaliseur sur toutes les données avant la séparation. Quelle approche est préférable ?', ('Accepter, car les étiquettes ne sont pas utilisées', 'Ajuster uniquement sur le test', 'Séparer d’abord, puis ajuster sur l’entraînement', 'Ne jamais normaliser'), 'Ajuster le prétraitement sur l’entraînement évite de divulguer des informations provenant des données réservées.', 'Les données réservées ne doivent pas influencer le prétraitement ajusté.'),
            ('Fondamentaux', 'Un modèle étiquette une photo « chat » ou « chien ». De quelle tâche s’agit-il ?', ('Régression', 'Classification', 'Regroupement', 'Réduction de dimension'), 'La classification prédit une catégorie parmi plusieurs catégories possibles.', 'La sortie est une catégorie nommée, pas un nombre.'),
            ('Fondamentaux', 'Pourquoi standardiser les caractéristiques numériques avant d’entraîner certains modèles ?', ('Pour comparer les échelles des caractéristiques', 'Pour créer de nouvelles étiquettes', 'Pour garantir une précision parfaite', 'Pour supprimer le besoin d’évaluation'), 'La standardisation remet les variables à une échelle comparable, ce qui aide les algorithmes sensibles aux échelles.', 'Pensez aux caractéristiques mesurées dans des unités très différentes.'),
            ('Évaluation', 'Sur 20 cas réellement positifs, un modèle en trouve correctement 15. Quel est son rappel ?', ('25 %', '60 %', '75 %', '90 %'), 'Rappel = vrais positifs / tous les positifs réels = 15 / 20 = 75 %.', 'Divisez les positifs trouvés par tous les positifs réels.'),
            ('Évaluation', 'Une équipe consulte plusieurs fois les résultats du jeu de test pendant l’ajustement du modèle. Quel est le risque principal ?', ('Le test influence le choix du modèle', 'Le test devient chiffré', 'Le modèle cesse d’utiliser des caractéristiques', 'Les étiquettes d’entraînement disparaissent'), 'Ajuster le modèle selon le test divulgue des informations du jeu d’évaluation dans le choix du modèle.', 'Le jeu de test doit rester intact jusqu’à la fin.'),
            ('Généralisation', 'Quel est le rôle principal d’un jeu de validation pendant le développement ?', ('Ajuster les choix sans utiliser le test final', 'Stocker la base de production', 'Remplacer tous les exemples d’entraînement', 'Garantir l’équité du modèle'), 'La validation aide à choisir et régler le modèle ; un jeu de test distinct sert à l’évaluation finale.', 'Il est utilisé avant le test final.'),
            ('Généralisation', 'Un jeu contient 990 cas négatifs et 10 positifs. Pourquoi la précision globale peut-elle induire en erreur ?', ('Tout prédire négatif peut tout de même donner 99 %', 'La précision ignore toujours les négatifs', 'Le jeu ne peut pas être séparé', 'La précision mesure la vitesse d’entraînement'), 'Avec des classes très déséquilibrées, un modèle peut obtenir une forte précision tout en manquant tous les cas minoritaires.', 'Imaginez un modèle qui prédit uniquement la classe majoritaire.'),
        ],
        'cyber': [
            ('Contrôle d’accès', 'À quelle question l’authentification répond-elle ?', ('À quoi pouvez-vous accéder ?', 'Qui êtes-vous ?', 'Le service est-il en ligne ?', 'Le fichier est-il chiffré ?'), 'L’authentification vérifie l’identité ; l’autorisation détermine les actions permises.', 'Pensez à la preuve d’identité.'),
            ('Contrôle d’accès', 'Modifier un numéro de facture permet de voir celle d’un autre client. Quel contrôle manque ?', ('Une URL plus longue', 'Une autorisation côté serveur pour cet objet', 'Une autre police', 'Un masquage côté client'), 'Le serveur doit vérifier que la personne peut accéder à cette facture précise.', 'Un utilisateur connecté a quand même besoin d’une permission pour chaque objet.'),
            ('Protection des données', 'Quelle opération est conçue pour être à sens unique ?', ('Chiffrement avec une clé', 'Encodage Base64', 'Hachage cryptographique', 'Compression de fichier'), 'Le hachage cryptographique résiste à l’inversion ; le chiffrement est réversible avec une clé.', 'Quelle opération n’a pas de clé de déchiffrement ?'),
            ('Protection des données', 'Quel est le meilleur choix pour stocker des mots de passe ?', ('Texte brut', 'Base64', 'Hachage dédié tel qu’Argon2 avec des sels', 'Chiffrement réversible avec une clé partagée'), 'Un hachage conçu pour les mots de passe et des sels protègent les vérificateurs stockés.', 'Utilisez un algorithme conçu spécifiquement pour les mots de passe.'),
            ('Développement sécurisé', 'Quelle défense standard protège contre l’injection SQL de valeurs utilisateur ?', ('Concaténation de chaînes', 'Requêtes paramétrées', 'Masquage du formulaire', 'Suppression des espaces'), 'Les requêtes paramétrées séparent les valeurs de la syntaxe SQL.', 'Traitez la saisie comme une donnée, pas comme de la syntaxe de requête.'),
            ('Développement sécurisé', 'Une IA affirme que HTTPS empêche toutes les failles d’autorisation. Quelle réponse est juste ?', ('Accepter sans condition', 'Désactiver HTTPS', 'Le chiffrement du transport ne contrôle pas les permissions sur les objets', 'Remplacer l’authentification par HTTPS'), 'HTTPS protège le transport ; une autorisation côté serveur reste nécessaire.', 'Protection du transport et permissions répondent à des problèmes différents.'),
            ('Contrôle d’accès', 'Après la connexion, que doit déterminer l’autorisation ?', ('Les actions et ressources accessibles à l’utilisateur', 'Le mot de passe', 'La connexion du câble réseau', 'Le thème visuel de la page'), 'L’autorisation évalue les droits d’un utilisateur sur une action et une ressource.', 'L’identité est établie ; pensez aux actions autorisées.'),
            ('Protection des données', 'Quel attribut de cookie empêche JavaScript côté client de lire le cookie de session ?', ('HttpOnly', 'Public', 'Indexable', 'Debug'), 'HttpOnly empêche les scripts du navigateur de lire le cookie via document.cookie.', 'Il limite l’accès des scripts exécutés dans la page.'),
            ('Développement sécurisé', 'Quelle conception est plus sûre pour un jeton de réinitialisation de mot de passe ?', ('Long, aléatoire, à durée courte et à usage unique', 'Nom prévisible et date actuelle', 'Jeton permanent commun à tous', 'Jeton dans un profil public'), 'Un jeton doit être imprévisible, expirer et devenir invalide après utilisation.', 'Un lien de réinitialisation ne doit pas être réutilisable indéfiniment.'),
            ('Contrôle d’accès', 'Que réduit la limitation du débit sur une connexion ?', ('Les tentatives automatisées répétées', 'La force du chiffrement', 'Le besoin d’autorisation', 'Le nombre de rôles'), 'La limitation du débit freine les tentatives de connexion répétées.', 'Pensez aux tentatives de deviner un mot de passe en série.'),
            ('Protection des données', 'Que protège le chiffrement au repos ?', ('Les données stockées sur un appareil ou un système', 'Tous les comptes contre les défauts d’autorisation', 'Un mot de passe oublié', 'Toutes les données contre une application autorisée'), 'Le chiffrement protège les données stockées si le support est exposé, mais ne remplace pas les contrôles d’accès.', 'Pensez aux données lorsqu’elles sont stockées plutôt qu’en transit.'),
            ('Développement sécurisé', 'Quelle défense aide un navigateur à rejeter les requêtes forgées avec une session connectée ?', ('Protection CSRF par jetons ou contrôles SameSite adaptés', 'Un titre de page plus long', 'Validation du formulaire côté client uniquement', 'Menu de navigation masqué'), 'Les défenses CSRF vérifient que les requêtes modifiant l’état ont été lancées intentionnellement par l’application.', 'Le risque concerne une requête authentifiée indésirable envoyée par le navigateur.'),
        ],
        'ai': [
            ('Recherche', 'Quelle recherche non informée explore les nœuds niveau par niveau ?', ('Recherche en profondeur', 'Recherche en largeur', 'Recherche aléatoire', 'Escalade de colline'), 'La recherche en largeur explore d’abord les nœuds les moins profonds.', 'Pensez à une file plutôt qu’à une pile.'),
            ('Recherche', 'Dans un graphe fini à coûts d’arêtes positifs égaux, quelle recherche trouve un plus court chemin avec gestion des états répétés ?', ('Recherche en largeur', 'Toujours en profondeur', 'Toute marche aléatoire', 'Toujours gloutonne'), 'Avec des coûts égaux, minimiser le nombre d’arêtes minimise le coût ; la recherche en largeur explore par profondeur.', 'Ici, la solution la moins profonde a le coût le plus bas.'),
            ('Systèmes d’apprentissage', 'Quel est un exemple d’apprentissage supervisé ?', ('Regrouper des actualités non étiquetées', 'Entraîner avec des images associées à des étiquettes', 'Parcourir un labyrinthe sans exemples', 'Écrire chaque règle à la main'), 'L’apprentissage supervisé utilise des exemples d’entrée associés à des étiquettes cibles.', 'Cherchez les exemples étiquetés.'),
            ('Systèmes d’apprentissage', 'En apprentissage par renforcement, quel retour guide l’agent vers son objectif ?', ('Noms de fichiers uniquement', 'Récompenses', 'Étiquette correcte obligatoire pour chaque action', 'Ordre alphabétique'), 'L’agent apprend grâce aux récompenses et à ses interactions avec l’environnement.', 'Le retour peut arriver après l’action.'),
            ('Vérification de l’IA', 'Un chatbot cite avec assurance un article de recherche. Quelle vérification est la plus utile ?', ('Faire confiance à son assurance', 'Trouver l’article original et vérifier qu’il étaye l’affirmation', 'Lui demander plus d’assurance', 'Compter les mots'), 'La source originale permet de vérifier son existence et son lien avec l’affirmation.', 'Vérifiez avec une source primaire.'),
            ('Vérification de l’IA', 'Pourquoi traiter avec prudence les consignes dans une page récupérée par un assistant IA ?', ('Toutes les pages sont fausses', 'Elles peuvent détourner l’assistant de la demande', 'La récupération chiffre toujours le texte', 'Les pages ne peuvent pas contenir de consignes'), 'Le contenu récupéré n’est pas fiable et peut contenir des tentatives d’injection de consignes.', 'Un contenu externe n’est pas automatiquement une consigne autorisée.'),
            ('Systèmes d’apprentissage', 'Que permet l’auto-attention dans un transformeur ?', ('Relier des jetons du même contexte', 'Chiffrer chaque jeton', 'Garantir la vérité de chaque sortie', 'Éliminer le besoin de données d’entraînement'), 'L’auto-attention calcule les relations contextuelles entre les jetons.', 'Un jeton peut utiliser des informations provenant d’autres jetons.'),
            ('Systèmes d’apprentissage', 'Quelle technique peut limiter le surapprentissage en pénalisant les modèles trop complexes ?', ('Régularisation', 'Dupliquer le test', 'Retirer les données de validation', 'Accroître la fuite d’étiquettes'), 'La régularisation ajoute une contrainte ou une pénalité qui décourage une complexité excessive.', 'Pensez à une pénalité appliquée à certains poids du modèle.'),
            ('Recherche', 'En apprentissage automatique, qu’est-ce qu’un plongement (embedding) ?', ('Une représentation vectorielle d’un élément comme un mot ou une image', 'Un score final de test', 'Une clé de chiffrement de mot de passe', 'Une simple liste d’erreurs'), 'Un plongement représente un élément par un vecteur exploitable par un modèle.', 'Il représente l’élément par des nombres dans un espace appris.'),
            ('Vérification de l’IA', 'Quelle première étape aide à vérifier l’exactitude factuelle d’une réponse générée par IA ?', ('Vérifier les affirmations importantes avec des sources primaires fiables', 'Croire la réponse la plus fluide', 'Supposer les citations valides sans les ouvrir', 'Demander plus d’assurance au modèle'), 'Vérifier les affirmations avec des sources fiables apporte des preuves au-delà de la formulation.', 'Cherchez des preuves externes, pas un style convaincant.'),
            ('Vérification de l’IA', 'Pourquoi séparer généralement les exemples d’entraînement et de test ?', ('Estimer les performances sur des données non utilisées pour l’ajustement', 'Rendre tous les exemples identiques', 'Supprimer définitivement la cible', 'Éviter de mesurer les performances'), 'Un jeu de test réservé aide à estimer la généralisation à des exemples inconnus.', 'L’estimation est plus utile si les données de test n’ont pas servi à l’ajustement.'),
            ('Recherche', 'Quelle limite importante concerne la génération augmentée par récupération (RAG) ?', ('Les documents récupérés peuvent être incomplets ou non pertinents ; les affirmations doivent être vérifiées', 'Elle ne peut pas utiliser de documents', 'La récupération garantit des réponses correctes', 'Elle entraîne toujours un nouveau modèle'), 'La récupération apporte du contexte, mais des erreurs de récupération et de génération restent possibles.', 'Des références ne garantissent pas à elles seules la justesse.'),
        ],
    },
    'ar': {
        'ml': [
            ('الأساسيات', 'توقّع سعر بيع منزل يُعد عادةً أي نوع من المهام؟', ('التصنيف', 'الانحدار', 'التجميع', 'التنقيب عن الترابط'), 'السعر قيمة رقمية مستهدفة، لذا فهذه مهمة انحدار.', 'هل الهدف فئة أم قيمة رقمية؟'),
            ('الأساسيات', 'أي أسلوب يجمع الأمثلة المتشابهة من دون استخدام تسميات مستهدفة؟', ('التصنيف الخاضع للإشراف', 'الانحدار الخطي', 'التجميع', 'الترتيب الخاضع للإشراف'), 'يكتشف التجميع مجموعات في بيانات غير موسومة.', 'ابحث عن أسلوب غير خاضع للإشراف.'),
            ('التقييم', 'صنّف نموذج 24 رسالة على أنها تصيّد. 18 منها تصيّد فعلاً. ما قيمة الدقة الإيجابية؟', ('25٪', '50٪', '75٪', '90٪'), 'الدقة الإيجابية = الإيجابيات الصحيحة / الإيجابيات المتوقعة = 18 / 24 = 75٪.', 'اقسم التوقعات الإيجابية الصحيحة على كل التوقعات الإيجابية.'),
            ('التقييم', 'أي مجموعة بيانات ينبغي تخصيصها للتقييم النهائي بعد اختيار النموذج؟', ('التدريب', 'الاختبار', 'نسخة مكررة من التدريب', 'الفئة الأكبر فقط'), 'تقدّر مجموعة اختبار لم تُمس الأداء بعد اختيار النموذج.', 'يجب ألا تؤثر في التدريب أو اختيار النموذج.'),
            ('التعميم', 'دقة التدريب 99٪ ودقة التحقق 65٪. ما المشكلة المحتملة؟', ('النموذج خالٍ بالتأكيد من التحيز', 'فرط التعلّم', 'تعميم مثالي', 'دقة تدريب منخفضة جداً'), 'تشير الفجوة الكبيرة إلى احتمال فرط التعلّم، مع وجود أسباب أخرى ممكنة.', 'قارن الأداء على الأمثلة التي شوهدت والتي لم تُشاهد.'),
            ('التعميم', 'اقترح نظام ذكاء اصطناعي ضبط أداة التحجيم على كامل البيانات قبل تقسيمها. ما الأفضل؟', ('الموافقة لأن التسميات غير مستخدمة', 'الضبط على الاختبار فقط', 'التقسيم أولاً ثم الضبط على التدريب', 'عدم تحجيم البيانات أبداً'), 'ضبط المعالجة المسبقة على بيانات التدريب يمنع تسرّب معلومات من البيانات المحجوزة.', 'يجب ألا تؤثر بيانات الاختبار في المعالجة المسبقة المضبوطة.'),
            ('الأساسيات', 'يصنّف نموذج صورة على أنها «قط» أو «كلب». ما نوع المهمة؟', ('الانحدار', 'التصنيف', 'التجميع', 'تقليل الأبعاد'), 'يتنبأ التصنيف بفئة من مجموعة فئات ممكنة.', 'الناتج فئة مسماة، وليس رقماً.'),
            ('الأساسيات', 'لماذا قد توحّد المقاييس الرقمية قبل تدريب بعض النماذج؟', ('لوضع السمات على مقاييس قابلة للمقارنة', 'لإنشاء تسميات مستهدفة', 'لضمان دقة مثالية', 'لإلغاء الحاجة إلى التقييم'), 'تعيد المعايرة السمات إلى مقاييس متقاربة، ما يفيد الخوارزميات الحساسة للمقياس.', 'فكّر في سمات مقاسة بوحدات مختلفة جداً.'),
            ('التقييم', 'من بين 20 حالة إيجابية فعلية، اكتشف النموذج 15 بشكل صحيح. ما قيمة الاستدعاء؟', ('25٪', '60٪', '75٪', '90٪'), 'الاستدعاء = الإيجابيات الصحيحة / كل الحالات الإيجابية الفعلية = 15 / 20 = 75٪.', 'اقسم الإيجابيات المكتشفة على كل الإيجابيات الفعلية.'),
            ('التقييم', 'يفحص فريق نتائج الاختبار مراراً أثناء ضبط النموذج. ما الخطر الأساسي؟', ('أن تصبح مجموعة الاختبار جزءاً من اختيار النموذج', 'أن تُشفّر مجموعة الاختبار', 'أن يتوقف النموذج عن استخدام السمات', 'أن تختفي تسميات التدريب'), 'الضبط اعتماداً على نتائج الاختبار يسرّب معلومات التقييم إلى عملية اختيار النموذج.', 'ينبغي إبقاء الاختبار دون مساس حتى النهاية.'),
            ('التعميم', 'ما الغرض الرئيسي من مجموعة التحقق أثناء تطوير النموذج؟', ('ضبط الخيارات دون استخدام الاختبار النهائي', 'تخزين قاعدة بيانات الإنتاج', 'استبدال كل أمثلة التدريب', 'ضمان عدالة النموذج'), 'تساعد مجموعة التحقق في اختيار النموذج وضبطه؛ وتبقى مجموعة اختبار مستقلة للتقييم النهائي.', 'تُستخدم قبل الاختبار النهائي.'),
            ('التعميم', 'تحتوي مجموعة بيانات على 990 حالة سلبية و10 إيجابية. لماذا قد تكون الدقة مضللة؟', ('قد تحقق توقعات السلبية دائماً دقة 99٪', 'الدقة تتجاهل السلبيات دائماً', 'لا يمكن تقسيم البيانات', 'الدقة تقيس سرعة التدريب'), 'قد تحقق النماذج دقة مرتفعة مع تفويت كل الفئة الأقلية عند عدم توازن الفئات.', 'تخيّل نموذجاً يتنبأ بالفئة الأكبر فقط.'),
        ],
        'cyber': [
            ('التحكم في الوصول', 'عن أي سؤال تجيب المصادقة؟', ('ما الذي يمكنك الوصول إليه؟', 'من أنت؟', 'هل الخدمة متصلة؟', 'هل الملف مشفّر؟'), 'تتحقق المصادقة من الهوية، بينما يحدد التفويض الإجراءات المسموح بها.', 'فكّر في إثبات الهوية.'),
            ('التحكم في الوصول', 'يؤدي تغيير رقم الفاتورة إلى كشف فاتورة عميل آخر. ما الضابط المفقود؟', ('عنوان URL أطول', 'تفويض الكائن من جهة الخادم', 'خط مختلف', 'إخفاء من جهة العميل'), 'يجب أن يتحقق الخادم من حق المستخدم في الوصول إلى تلك الفاتورة تحديداً.', 'يحتاج المستخدم المسجّل إلى إذن لكل كائن.'),
            ('حماية البيانات', 'أي عملية صُممت لتكون أحادية الاتجاه؟', ('التشفير بمفتاح', 'ترميز Base64', 'التجزئة التشفيرية', 'ضغط الملفات'), 'تقاوم التجزئة التشفيرية عكس العملية، بينما يمكن فك التشفير بمفتاح.', 'أي عملية لا تملك مفتاح فك تشفير؟'),
            ('حماية البيانات', 'ما الخيار الأفضل لتخزين كلمات مرور المستخدمين؟', ('نص واضح', 'Base64', 'تجزئة مخصصة مثل Argon2 مع أملاح', 'تشفير قابل للعكس بمفتاح مشترك'), 'تحمي خوارزمية تجزئة مخصصة مع أملاح بيانات التحقق المخزنة لكلمات المرور.', 'استخدم خوارزمية صُممت خصيصاً لكلمات المرور.'),
            ('التطوير الآمن', 'ما الدفاع القياسي ضد حقن SQL عند إدخال قيم المستخدم في الاستعلام؟', ('دمج السلاسل النصية', 'استعلامات ذات معاملات', 'إخفاء النموذج', 'حذف المسافات'), 'تفصل الاستعلامات ذات المعاملات القيم عن صياغة SQL.', 'تعامل مع المدخلات كبيانات لا كصياغة استعلام.'),
            ('التطوير الآمن', 'يقول نظام ذكاء اصطناعي إن HTTPS يمنع كل عيوب التفويض. ما الرد الصحيح؟', ('الموافقة دون شرط', 'تعطيل HTTPS', 'تشفير النقل لا يفرض صلاحيات الوصول إلى الكائنات', 'استبدال المصادقة بـ HTTPS'), 'يحمي HTTPS البيانات أثناء النقل، لكن التفويض على الخادم يظل ضرورياً.', 'حماية النقل والصلاحيات تعالجان مشكلتين مختلفتين.'),
            ('التحكم في الوصول', 'ماذا ينبغي أن يحدد التفويض بعد تسجيل الدخول؟', ('الإجراءات والموارد المسموح بها للمستخدم', 'كلمة مرور المستخدم', 'اتصال كابل الشبكة', 'مظهر الصفحة'), 'يقيّم التفويض أذونات المستخدم والإجراء والمورد.', 'تم التحقق من الهوية؛ فكّر في الإجراءات المسموحة.'),
            ('حماية البيانات', 'أي إعداد لملفات تعريف الارتباط يمنع JavaScript من قراءة ملف الجلسة؟', ('HttpOnly', 'Public', 'Indexable', 'Debug'), 'يمنع HttpOnly نصوص المتصفح من قراءة ملف الارتباط عبر document.cookie.', 'يحدّ من وصول النصوص البرمجية التي تعمل في الصفحة.'),
            ('التطوير الآمن', 'ما التصميم الأكثر أماناً لرمز إعادة تعيين كلمة المرور؟', ('عشوائي طويل وقصير الصلاحية ويُستخدم مرة واحدة', 'اسم مستخدم متوقع مع تاريخ اليوم', 'رمز دائم مشترك بين الجميع', 'رمز في ملف عام'), 'ينبغي أن يكون الرمز غير متوقع ومنتهي الصلاحية وغير صالح بعد استخدامه.', 'يجب ألا يكون رابط إعادة التعيين قابلاً للاستخدام إلى الأبد.'),
            ('التحكم في الوصول', 'ما الذي يساعد تحديد المعدل على تقليله في صفحة تسجيل الدخول؟', ('محاولات التخمين الآلية المتكررة', 'قوة التشفير', 'الحاجة إلى التفويض', 'عدد أدوار المستخدمين'), 'يحدّ تحديد المعدل من تكرار محاولات تسجيل الدخول.', 'فكّر في المحاولات المتتابعة لتخمين كلمة المرور.'),
            ('حماية البيانات', 'ممَّ يحمي التشفير أثناء التخزين؟', ('البيانات المخزنة على جهاز أو نظام تخزين', 'كل الحسابات من عيوب التفويض', 'نسيان كلمة المرور', 'كل البيانات من تطبيق مخوّل'), 'يحمي التشفير البيانات المخزنة عند انكشاف وسيط التخزين، لكنه لا يستبدل ضوابط الوصول.', 'فكّر في البيانات وهي مخزنة لا منقولة.'),
            ('التطوير الآمن', 'ما الدفاع الذي يساعد المتصفح على رفض الطلبات المزورة بجلسة مسجّلة؟', ('حماية CSRF برموز أو ضوابط SameSite مناسبة', 'عنوان صفحة أطول', 'التحقق من النموذج على العميل فقط', 'إخفاء قائمة التنقل'), 'تتحقق دفاعات CSRF من أن الطلبات المغيّرة للحالة بدأها التطبيق عن قصد.', 'يتعلق الخطر بطلب مصادق عليه غير مرغوب يرسله المتصفح.'),
        ],
        'ai': [
            ('البحث', 'أي بحث غير موجّه يستكشف العقد مستوىً بعد مستوى؟', ('البحث بالعمق أولاً', 'البحث بالعرض أولاً', 'البحث العشوائي', 'تسلق التل'), 'يستكشف البحث بالعرض أولاً العقد الأقرب إلى البداية.', 'فكّر في طابور بدلاً من مكدّس.'),
            ('البحث', 'في رسم بياني محدود ذي تكاليف حواف موجبة ومتساوية، أي بحث يجد أقصر مسار مع معالجة الحالات المتكررة؟', ('البحث بالعرض أولاً', 'البحث بالعمق دائماً', 'أي مسير عشوائي', 'البحث الجشع دائماً'), 'عند تساوي التكاليف، يقلل عدد الحواف تكلفة المسار؛ ويستكشف البحث بالعرض حسب العمق.', 'الحل الأقل عمقاً هو الأقل تكلفة هنا.'),
            ('أنظمة التعلّم', 'أي مما يلي مثال على التعلّم الخاضع للإشراف؟', ('تجميع أخبار غير موسومة', 'التدريب على صور مقترنة بتسميات الفئات', 'استكشاف متاهة دون أمثلة', 'كتابة كل قاعدة يدوياً'), 'يستخدم التعلّم الخاضع للإشراف أمثلة إدخال مع تسميات مستهدفة.', 'ابحث عن أمثلة موسومة.'),
            ('أنظمة التعلّم', 'في التعلّم المعزز، ما التغذية الراجعة التي توجه الوكيل نحو هدفه؟', ('أسماء الملفات فقط', 'المكافآت', 'تسمية صحيحة إلزامية لكل إجراء', 'الترتيب الأبجدي'), 'يتعلم الوكيل من المكافآت والتفاعل مع البيئة.', 'قد تصل التغذية الراجعة بعد الإجراء.'),
            ('التحقق من الذكاء الاصطناعي', 'استشهد روبوت محادثة بثقة بورقة بحثية. ما أفضل خطوة للتحقق؟', ('الثقة بنبرة الروبوت', 'العثور على الورقة الأصلية والتحقق من أنها تدعم الادعاء', 'طلب نبرة أكثر ثقة', 'عدّ الكلمات'), 'يختبر المصدر الأصلي وجود الورقة ومدى دعمه للادعاء.', 'تحقق من الدليل الأساسي.'),
            ('التحقق من الذكاء الاصطناعي', 'لماذا ينبغي الحذر من التعليمات داخل صفحة استرجعها مساعد ذكي؟', ('كل صفحات الويب خاطئة', 'قد تحاول تحويل المساعد عن مهمة المستخدم', 'الاسترجاع يشفّر النص دائماً', 'لا تحتوي الصفحات على تعليمات'), 'المحتوى المسترجع دليل غير موثوق وقد يتضمن محاولات لحقن التعليمات.', 'المحتوى الخارجي ليس تعليمة مخوّلة تلقائياً.'),
            ('أنظمة التعلّم', 'ما الذي يساعد الانتباه الذاتي نموذج المحوّل على فعله؟', ('ربط الرموز بعضها ببعض ضمن السياق', 'تشفير كل رمز إدخال', 'ضمان صحة كل ناتج', 'إلغاء الحاجة إلى بيانات التدريب'), 'يحسب الانتباه الذاتي العلاقات السياقية بين الرموز عند بناء التمثيلات.', 'يستفيد الرمز من معلومات الرموز الأخرى.'),
            ('أنظمة التعلّم', 'أي تقنية قد تقلل فرط التعلّم عبر الحد من تعقيد النموذج؟', ('التنظيم', 'تكرار مجموعة الاختبار', 'إزالة بيانات التحقق', 'زيادة تسرّب التسميات'), 'يضيف التنظيم قيداً أو عقوبة تحد من التعقيد المفرط.', 'فكّر في عقوبة على بعض أوزان النموذج.'),
            ('البحث', 'ما التضمين في تعلّم الآلة؟', ('تمثيل متجهي لعنصر مثل كلمة أو صورة', 'نتيجة الاختبار النهائية', 'مفتاح تشفير كلمة مرور', 'قائمة أخطاء فقط'), 'يحوّل التضمين العنصر إلى متجه يمكن للنموذج استخدام سماته وعلاقاته.', 'يمثل العنصر بأرقام ضمن فضاء متعلّم.'),
            ('التحقق من الذكاء الاصطناعي', 'ما خطوة أولى مفيدة عند تقييم الدقة الواقعية لإجابة مولّدة؟', ('فحص الادعاءات المهمة بمصادر أولية موثوقة', 'الثقة بالإجابة الأكثر سلاسة', 'افتراض صحة المراجع دون فتحها', 'طلب ثقة أعلى من النموذج'), 'يوفر التحقق من الادعاءات بمصادر موثوقة دليلاً يتجاوز صياغة النموذج.', 'ابحث عن دليل خارجي لا عن أسلوب العرض.'),
            ('التحقق من الذكاء الاصطناعي', 'لماذا تُفصل عادةً أمثلة التدريب والاختبار؟', ('لتقدير الأداء على بيانات لم تُستخدم في الملاءمة', 'لجعل كل الأمثلة متطابقة', 'لحذف المتغير المستهدف نهائياً', 'لتجنب قياس الأداء'), 'تساعد مجموعة اختبار محجوزة على تقدير التعميم على أمثلة غير مرئية.', 'يكون التقدير أدق عندما لا تُستخدم بيانات التقييم في التدريب.'),
            ('البحث', 'ما أحد القيود المهمة للتوليد المعزّز بالاسترجاع؟', ('قد تكون المواد المسترجعة ناقصة أو غير ملائمة، لذا يجب التحقق من الادعاءات', 'لا يمكنه استخدام المستندات', 'يضمن الاسترجاع إجابات صحيحة', 'يدرّب دائماً نموذجاً جديداً'), 'يوفر الاسترجاع سياقاً مفيداً، لكن أخطاء الاسترجاع والتوليد تظل ممكنة.', 'إضافة المراجع لا تضمن الصحة وحدها.'),
        ],
    },
}


def localize_question(question, language='en'):
    if language not in QUESTION_TEXT:
        return question
    localized_bank = QUESTION_TEXT[language]
    for subject, questions in BANK.items():
        for index, original in enumerate(questions):
            if original['id'] != question.get('id'):
                continue
            if index >= len(localized_bank[subject]):
                translated = deepcopy(question)
                translated['topic'] = TOPIC_TRANSLATIONS.get(
                    language,
                    {},
                ).get(question['topic'], question['topic'])
                return translated
            topic, prompt, choices, explanation, hint = localized_bank[subject][index]
            translated = deepcopy(question)
            translated.update(
                topic=topic,
                prompt=prompt,
                choices=list(choices),
                explanation=explanation,
                hint=hint,
            )
            return translated
    return question


def localize_subject(subject, language='en'):
    return SUBJECT_TRANSLATIONS.get(language, {}).get(subject, SUBJECTS[subject])


def localize_topic(topic, language='en'):
    return TOPIC_TRANSLATIONS.get(language, {}).get(topic, topic)
