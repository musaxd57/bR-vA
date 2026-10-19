import os
import json
import traceback
from datetime import datetime
from dotenv import load_dotenv

# Veritabanı ve yardımcı fonksiyonları içe aktar
from db_utils import (
    add_product, 
    delete_product, 
    update_product,
    get_company_sales_stats,
    get_company_products,
    ai_query_product_by_code,
    ai_create_sale,
    ai_get_all_sales,
    ai_add_customer,
    ai_get_customer_debt,
    ai_add_expenditure,
    ai_manage_order,
    ai_list_pending_orders,
    ai_add_definition,
    ai_get_low_stock_products,
    delete_product_by_code_helper,
    ai_add_stock_to_shelf_helper,
    ai_list_shelves,
    ai_add_shelf
)

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Yerel kurulum bir AI anahtarı veya Groq paketi gerektirmez.
client = None
if os.environ.get("GROQ_API_KEY", "").strip():
    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
    except (ImportError, ValueError):
        client = None

# --- ARAÇ SETİ (TOOLS SCHEMA) ---
tools_schema = [
    # 1. RAF YÖNETİMİ
    {
        "type": "function",
        "function": {
            "name": "list_shelves_tool",
            "description": "Sistemde kayıtlı olan mevcut rafları listeler.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_shelf_tool",
            "description": "Sisteme yeni bir raf tanımlar.",
            "parameters": {
                "type": "object",
                "properties": { "code": {"type": "string", "description": "Eklenecek raf kodu (Örn: Z1)"} },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_stock_to_shelf_tool",
            "description": "Mevcut bir rafa ürün stoklar. Üründe tanımlı olmayan beden eklenemez.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_code": {"type": "string"},
                    "shelf_code": {"type": "string"},
                    "quantities": {"type": "object", "description": "Beden ve adet JSON. Örn: {'X': 10}"}
                },
                "required": ["product_code", "shelf_code", "quantities"],
            },
        },
    },

    # 2. ÜRÜN İŞLEMLERİ
    {
        "type": "function",
        "function": {
            "name": "query_product",
            "description": "Bir ürünün KODUNA veya İSMİNE göre detayını, RAF durumunu ve BEDENLERİNİ getirir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": { # Parametre adını 'code' olarak bıraktık ama açıklamasını değiştirdik
                        "type": "string", 
                        "description": "Ürün kodu VEYA ürün adı (Örn: '101' veya 'Mavi Gömlek')"
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_company_products",
            "description": "Tüm ürünleri listeler.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_product_tool",
            "description": "Yeni ürün kartı açar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"}, "name": {"type": "string"}, "category": {"type": "string"},
                    "price_tl": {"type": "number"}, "stock": {"type": "integer"}, "description": {"type": "string"}
                },
                "required": ["code", "name", "category", "price_tl", "stock"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_product_tool",
            "description": "Ürünü siler.",
            "parameters": {
                "type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"],
            },
        },
    },

    # 3. DİĞERLERİ
    {
        "type": "function",
        "function": {
            "name": "create_sale_tool",
            "description": "Satış yapar.",
            "parameters": {
                "type": "object", "properties": {"product_code": {"type": "string"}, "quantity": {"type": "integer"}, "customer_name": {"type": "string"}},
                "required": ["product_code", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sales_analysis",
            "description": "Satış analizi.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_customer_tool",
            "description": "Müşteri ekler.",
            "parameters": {
                "type": "object",
                "properties": { "name": {"type": "string"}, "phone": {"type": "string"}, "address": {"type": "string"}, "description": {"type": "string"} }, "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_debt",
            "description": "Müşteri borcu sorgular.",
            "parameters": {
                "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_expenditure_tool",
            "description": "Gider ekler.",
            "parameters": {
                "type": "object",
                "properties": { "amount": {"type": "number"}, "currency": {"type": "string"}, "description": {"type": "string"} }, "required": ["amount", "currency", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_order_tool",
            "description": "Sipariş onayı/reddi.",
            "parameters": {
                "type": "object",
                "properties": { "order_id": {"type": "integer"}, "action": {"type": "string"} }, "required": ["order_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pending_orders",
            "description": "Bekleyen siparişleri listeler.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_low_stock_products",
            "description": "Kritik stok raporu.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]

# --- FONKSİYON YÜRÜTME MOTORU ---
def execute_tool_call(tool_call, company_id, user_id):
    func_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    
    print(f"🔧 AI Tool: {func_name} | Args: {args} | User: {user_id}")

    try:
        # --- RAF İŞLEMLERİ ---
        if func_name == "list_shelves_tool":
            return ai_list_shelves(company_id)
        elif func_name == "add_shelf_tool":
            return ai_add_shelf(args.get("code"), company_id)
        elif func_name == "add_stock_to_shelf_tool":
            return ai_add_stock_to_shelf_helper(args.get("product_code"), args.get("shelf_code"), args.get("quantities"), company_id)

        # --- ÜRÜN İŞLEMLERİ ---
        elif func_name == "query_product":
            result = ai_query_product_by_code(args.get("code"), company_id)
            return json.dumps(result, ensure_ascii=False) if result else "Ürün bulunamadı."
        elif func_name == "get_company_products":
            products = get_company_products(company_id)
            return json.dumps([{ "name": p.name, "code": p.code, "stock": p.stock } for p in products], ensure_ascii=False)
        elif func_name == "add_product_tool":
            currency = {"TL": args.get("price_tl", 0), "USD": 0, "EURO": 0}
            res = add_product(code=args.get("code"), name=args.get("name"), company_id=company_id, category=args.get("category"), description=args.get("description", ""), stock_id=None, currency=currency, sizes=[{"value": "Standart", "stock": args.get("stock"), "number": 1}])
            return "Ürün eklendi." if res else "Hata oluştu."
        elif func_name == "delete_product_tool":
            res = delete_product_by_code_helper(args.get("code"), company_id)
            return "Ürün silindi." if res else "Silinemedi."

        # --- DİĞERLERİ ---
        elif func_name == "create_sale_tool":
            return json.dumps(ai_create_sale(args.get("product_code"), args.get("quantity"), company_id, user_id, args.get("customer_name")), ensure_ascii=False)
        elif func_name == "get_sales_analysis":
            return json.dumps(get_company_sales_stats(company_id), ensure_ascii=False, default=str)
        elif func_name == "add_customer_tool":
            return json.dumps(ai_add_customer(args.get("name"), args.get("phone"), args.get("address"), args.get("description"), company_id), ensure_ascii=False)
        elif func_name == "get_customer_debt":
            return ai_get_customer_debt(args.get("name"), company_id)
        elif func_name == "add_expenditure_tool":
            return ai_add_expenditure(args.get("amount"), args.get("currency", "TL"), args.get("description"), company_id, user_id)
        elif func_name == "manage_order_tool":
            return ai_manage_order(args.get("order_id"), args.get("action"), company_id, user_id)
        elif func_name == "list_pending_orders":
            return ai_list_pending_orders(company_id)
        elif func_name == "add_definition_tool":
            return ai_add_definition(args.get("type"), args.get("value"), company_id)
        elif func_name == "get_low_stock_products":
            return ai_get_low_stock_products(company_id)
        else:
            return "Fonksiyon bulunamadı."

    except Exception as e:
        traceback.print_exc()
        return f"İşlem hatası: {str(e)}"

# --- SOHBET FONKSİYONU ---
def get_groq_response(user_message, company_id, user_id, conversation_history=None):
    if not client: return "AI Servisi kullanılamıyor."

    # --- SİSTEM TALİMATI (ÜSLUP VE GİZLİLİK AYARLANDI) ---
    system_prompt = """
    Sen 'StokTika' adında, işletme sahiplerine yardımcı olan profesyonel ve samimi bir depo asistanısın.
    
    ÜSLUP VE KONUŞMA KURALLARI:
    1. **GİZLİLİK:** Asla teknik fonksiyon isimlerini (örn: 'add_product_tool', 'list_shelves_tool' vb.) kullanıcıya söyleme. Bunlar senin iç çalışma sırrın.
    2. **DOĞALLIK:** "Hangi fonksiyonu kullanayım?" veya "Tool çağırıyorum" deme. "Hemen hallediyorum", "İşlemi yaptım", "Şunları yapabilirim" gibi doğal ifadeler kullan.
    3. **ÖZETLEME:** "Neler yapabilirsin?" diye sorulduğunda teknik tablo verme. Şöyle özetle: "Stoklarınızı yönetebilirim, satış yapıp cironuzu takip edebilirim, müşteri ve borç kaydı tutabilirim."
    
    İŞLEM MANTIĞI KURALLARI:
    1. 'add_stock_to_shelf_tool' kullanırken ÜRÜNÜN MEVCUT BEDENLERİNE SADIK KAL. Veritabanında tanımlı olmayan bir beden eklenmeye çalışılırsa hata döner, bunu kullanıcıya ilet.
    2. Kullanıcı olmayan bir rafa işlem yapmak isterse, inisiyatif alma. "Bu raf yok, oluşturmamı ister misin?" diye sor. Onay gelirse önce rafı oluştur, sonra stoku ekle.
    3. Kullanıcı "Hangi raflar var?" derse veritabanından (tool kullanarak) listele.
    """

    messages = [{"role": "system", "content": system_prompt}]
    
    # Hafızayı ekle
    if conversation_history: 
        messages.extend(conversation_history[-6:]) 
    
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            tools=tools_schema,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=1024
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            # Temiz mesaj ekleme (Reasoning hatasını önlemek için)
            clean_message = {
                "role": "assistant",
                "content": response_message.content
            }
            if response_message.tool_calls:
                clean_message["tool_calls"] = response_message.tool_calls
            
            messages.append(clean_message)

            for tool_call in tool_calls:
                function_response = execute_tool_call(tool_call, company_id, user_id)
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": tool_call.function.name,
                    "content": str(function_response),
                })
            
            final_response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=messages,
                tools=tools_schema,
                tool_choice="none"
            )
            return final_response.choices[0].message.content

        return response_message.content

    except Exception as e:
        print(f"Groq Hatası: {e}")
        if "400" in str(e): return "İşlem gerçekleştirildi ancak cevap oluşturulurken teknik bir aksaklık oldu. Lütfen kontrol edin."
        return "Bağlantı hatası oluştu."

