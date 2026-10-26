"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the step-by-step solution files and INSTRUCTIONS.md for guidance.
Do NOT copy the solution directly — work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
#from mcp.client.streamable_http import streamable_http_client
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Any, Dict, List, Optional
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

# TODO: Create the BedrockAgentCoreApp instance
app = BedrockAgentCoreApp()  # Replace this line


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in Part 1 of the INSTRUCTIONS.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customersupportgateway-ikb8wzrilr.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"     # TODO: Replace with your Gateway URL
KB_ID       = "NMGTQBNXSP"                                                                                          # TODO: Replace with your Knowledge Base ID
REGION      = "us-east-1"                                                                                           # TODO: Replace with your AWS region
MEMORY_ID   = "CustomerSupportMemory-YDjBX4A6kg"                                                                    # TODO: Replace with your Memory ID


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

model_id = "global.amazon.nova-2-lite-v1:0"

# TODO: Create the BedrockModel instance
model = BedrockModel(model_id=model_id)  # Replace this line

# TODO: Create the MemoryClient instance
memory_client = MemoryClient(region_name=REGION)  # Replace this line

# TODO: Create the boto3 bedrock-agent-runtime client
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)  # Replace this line


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Return a dict: { strategy["type"]: strategy["namespaces"][0] for each strategy }
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    # TODO: Implement this function
    strategy_dict = {strategy["type"]: strategy["namespaces"][0] for strategy in mem_client.get_memory_strategies(memory_id)}
    return strategy_dict  


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""
    CONTEXT_PREFIX = "Customer Context:\n"

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        # TODO: Call get_namespaces() and store the result as self.namespaces
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_id = memory_id
        self.memory_client = memory_client
        self.namespaces = get_namespaces(self.memory_client, self.memory_id)
        self._original_query = None

    def _plain_text_message(self, message: Any) -> Optional[str]:
        """Return message text only when its content is plain text (not tool/media blocks)."""
        if not isinstance(message, dict):
            return None

        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            return text or None
        if not isinstance(content, list) or not content:
            return None

        text_parts: List[str] = []
        non_text_keys = {
            "toolUse", "toolResult", "image", "document", "video",
            "reasoningContent", "guardContent", "audio", "json",
        }
        for block in content:
            if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                return None
            if any(key in block for key in non_text_keys):
                return None
            text_parts.append(block["text"])

        text = "\n".join(text_parts).strip()
        return text or None
    
    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        # TODO: Implement memory retrieval
        # Steps:
        #   1. Get the last message from event.agent.messages
        #   2. Check it is a user message and not a tool result
        #   3. Extract the user query text
        #   4. For each namespace in self.namespaces, call retrieve_memories()
        #   5. Collect non-empty memory texts with strategy type tags
        #   6. If any found, prepend them to the user message
        try:
            messages = event.agent.messages
            if not messages:
                return
            last = messages[-1]
            if last.get("role") != "user":
                return
            
            user_query = self._plain_text_message(last)
            if user_query is None:
                return
 
            self._original_query = user_query
 
            collected = []
            for strategy_type, ns_template in self.namespaces.items():
                namespace = ns_template.format(actorId=self.actor_id)
                memories = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=namespace,
                    query=user_query,
                    top_k=5,
                )
                for mem in memories or []:
                    text = (mem.get("content") or {}).get("text", "").strip()
                    if text:
                        collected.append(f"[{strategy_type}] {text}")
 
            if collected:
                context = "\n".join(collected)
                last["content"][0]["text"] = (
                    f"{self.CONTEXT_PREFIX}{context}\n\n{user_query}"
                )
        except Exception as e:
            logger.error(f"Memory retrieval failed: {e}")

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        # TODO: Implement memory saving
        # Steps:
        #   1. Get messages from event.agent.messages
        #   2. Walk backwards to find the last user query (plain text)
        #      and the last assistant response
        #   3. Call memory_client.create_event() with both messages
        try:
            messages = event.agent.messages
            customer_query = None
            agent_response = None
 
            for msg in reversed(messages):
                content = msg.get("content") or []
                if not content or "text" not in content[0]:
                    continue
                if msg.get("role") == "assistant" and agent_response is None:
                    agent_response = content[0]["text"]
                elif msg.get("role") == "user" and customer_query is None:
                    customer_query = content[0]["text"]
                if customer_query and agent_response:
                    break
 
            # Prefer the original query (without the injected "Customer Context").
            if self._original_query:
                customer_query = self._original_query
            elif customer_query and customer_query.startswith(self.CONTEXT_PREFIX):
                customer_query = customer_query.split("\n\n")[-1]
 
            if customer_query and agent_response:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[
                        (customer_query, "USER"),
                        (agent_response, "ASSISTANT"),
                    ],
                )
        except Exception as e:
            logger.error(f"Memory save failed: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        # TODO: Register retrieve_customer_context on MessageAddedEvent
        # TODO: Register save_support_interaction on AfterInvocationEvent
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    # TODO: Implement the Knowledge Base search
    if not KB_ID:
        return "Knowledge base not configured."
    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
        results = resp.get("retrievalResults", [])
        if not results:
            return "No relevant information found in the knowledge base."
        chunks = [
            r["content"]["text"]
            for r in results
            if r.get("content", {}).get("text")
        ]
        if not chunks:
            return "No relevant information found in the knowledge base."
        return "\n---\n".join(chunks)
    except Exception as e:
        logger.error(f"Knowledge base search failed: {e}")
        return f"Knowledge base search failed: {e}"


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

POINT_VALUE_USD = 0.01  # Value of one loyalty point in USD (e.g., 1 point = $0.01)

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # TODO: Build the code string (use an f-string to inject the arguments)
    code = f"""
import json, math
 
earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}
 
loyalty_points = {int(loyalty_points)}
tier = {tier!r}
order_total = {float(order_total)}
product_category = {product_category!r}
point_value = {POINT_VALUE_USD}
 
# Points redemption: floor to nearest 500, capped at 50% of the order
max_points_by_order = int((order_total * 0.5) / point_value)
redeemable = min(loyalty_points, max_points_by_order)
points_redeemed = (redeemable // 500) * 500
points_value = round(points_redeemed * point_value, 2)
 
# Tier discount applies to the subtotal after points
subtotal_after_points = round(order_total - points_value, 2)
tier_rate = tier_rates.get(tier, 0.0)
tier_discount = round(subtotal_after_points * tier_rate, 2)
 
final_total = round(subtotal_after_points - tier_discount, 2)
total_savings = round(order_total - final_total, 2)
 
earn_rate = earn_rates.get(product_category, 1)
points_earned = int(math.floor(final_total * earn_rate))
remaining_points = loyalty_points - points_redeemed
 
print(json.dumps({{
    "tier": tier,
    "product_category": product_category,
    "order_total": order_total,
    "points_redeemed": points_redeemed,
    "points_value": points_value,
    "subtotal_after_points": subtotal_after_points,
    "tier_discount_pct": round(tier_rate * 100, 2),
    "tier_discount": tier_discount,
    "final_total": final_total,
    "total_savings": total_savings,
    "points_earned": points_earned,
    "remaining_points": remaining_points,
}}))
"""  

    # TODO: Execute the code using code_session and return the result
    try:
        with code_session(REGION) as session:
            response = session.invoke(
                "executeCode",
                {
                    "code": code,
                    "language": "python",
                    "clearContext": True,
                },
            )
            for event in response["stream"]:
                stdout = event["result"]["structuredContent"]["stdout"]
                return stdout.strip()
        return json.dumps({"error": "Code Interpreter returned no result."})

    # TODO: Implement fallback calculation using tier discount only
    except Exception as e:

        logger.error(f"Code Interpreter unavailable, using fallback: {e}")
        tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
        rate = tier_rates.get(tier, 0.0)
        discount = round(order_total * rate, 2)
        return json.dumps({
            "note": "Fallback calculation (tier discount only; points not applied).",
            "calculation_mode": "fallback",
            "tier": tier,
            "order_total": order_total,
            "points_redeemed": 0,
            "tier_discount_pct": round(rate * 100, 2),
            "tier_discount": discount,
            "final_total": round(order_total - discount, 2),
            "remaining_points": loyalty_points,
            })

# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

SYSTEM_PROMPT = """You are a helpful, professional customer support agent.
 
Tools:
- search_knowledge_base: use for product specs, return policies, warranty info,
  loyalty program details, and order status definitions.
- calculate_loyalty_discount: use whenever a customer asks about discounts,
  points redemption, or final price. Never do this arithmetic yourself.
- browser: use only when information is needed from a live web page.
- Gateway tools: use for customer and order lookups they provide.
 
Guidelines:
- Use "Customer Context" (if present) to personalise answers, but do not
  mention that it came from memory.
- Be concise, accurate, and polite. If you don't know something, say so
  rather than guessing.
"""

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    # TODO: Implement the agent invocation
    try:
        user_input = payload.get("prompt", "")
        actor_id = payload.get("customer_id", "anonymous")
        session_id = payload.get("session_id") or str(uuid.uuid4())
 
        if not user_input:
            return "Please provide a message so I can help you."
 
        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )
 
        agent_core_browser = AgentCoreBrowser(region=REGION)
 
        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]
 
        # uses a bearer token, pass headers={"Authorization": f"Bearer {token}"}.
        mcp_client = MCPClient(
                                lambda: aws_iam_streamablehttp_client(
                                    endpoint=GATEWAY_URL,
                                    aws_region=REGION,
                                    aws_service="bedrock-agentcore",
                                )
                            )
        # Tools and the agent call must both run inside the `with` block.
        with mcp_client:
            gateway_tools = mcp_client.list_tools_sync()
            tools.extend(gateway_tools)
 
            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=SYSTEM_PROMPT,
            )
            response = agent(user_input)
 
        return response.message["content"][0]["text"]
 
    except Exception as e:
        logger.exception(f"Agent invocation failed: {e}")
        return "I'm sorry, something went wrong while processing your request. Please try again."


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
