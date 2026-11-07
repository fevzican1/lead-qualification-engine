from pact import Pact
from pact import interaction
from pact.match import like, regex
import inspect

# Check the upon_receiving method signature
print("=== upon_receiving signature ===")
sig = inspect.signature(Pact.upon_receiving)
print(sig)

# Check upon_receiving returns
print("\n=== upon_receiving doc ===")
print(Pact.upon_receiving.__doc__)

# Check HttpInteraction signature via the interaction module
HI = interaction.HttpInteraction
print("\n=== HttpInteraction.with_request signature ===")
sig = inspect.signature(HI.with_request)
print(sig)

print("\n=== HttpInteraction.will_respond_with signature ===")
sig = inspect.signature(HI.will_respond_with)
print(sig)

# Check if there's a body property or method
print("\n=== HttpInteraction attributes ===")
print(dir(HI))

# Check with_body signature
print("\n=== HttpInteraction.with_body signature ===")
sig = inspect.signature(HI.with_body)
print(sig)

# Check with_generators signature
print("\n=== HttpInteraction.with_generators signature ===")
sig = inspect.signature(HI.with_generators)
print(sig)