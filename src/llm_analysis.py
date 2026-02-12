import openai


ANALYSIS_PROMPT = """Analyze this image from a doorbell camera that was triggered by motion.

Your task is to determine if there's a potential package thief in this image or if there is a package being dropped off
by a delivery driver

Look for these suspicious behaviors:
1. Someone picking up a package that was left at the door
2. Someone looking around suspiciously while near packages
3. Someone quickly grabbing something and leaving
4. Someone who doesn't appear to be a delivery person taking or dropping off a package
5. Multiple people where one acts as a lookout

Also consider these innocent scenarios:
- The homeowner retrieving their own package
- A delivery person dropping off a package
- A neighbor or expected visitor
- Just motion from animals, cars, or wind

Respond with a JSON object:
{
    "is_suspicious": true/false, 
    "confidence_of_suspicion": "high"/"medium"/"low",
    "is_delivery" : true/false,
    "description": "Brief description of what you see",
    "reason": "Why you flagged or didn't flag this as suspicious"
}

Only set is_suspicious to true if you have medium or high confidence that package theft 
is occurring or about to occur. When in doubt, err on the side of caution (false positive 
is better than missing a thief).

Only set is_delivery to true if you ascertain that a delivery driver is dropping off a package
"""
class DeterminationEngine():

    def __init__(self, openai_api_key = None, claude_api_key = None):
        self.openai_api_key = openai_api_key
        self.claude_api_key = claude_api_key
    
    async def analyze_image_for_theft(
        self,
        image_data: bytes,
    ) -> dict:
        """
        Send image to OpenAI GPT Vision API for package thief analysis.

        Args:
            image_data: JPEG image bytes
            api_key: OpenAI API key

        Returns:
            Analysis result dict with is_suspicious, confidence, description, reason
        """
        client = openai.OpenAI(api_key=self.openai_api_key)

        image_base64 = base64.standard_b64encode(image_data).decode("utf-8")

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}",
                                },
                            },
                            {
                                "type": "text",
                                "text": ANALYSIS_PROMPT,
                            },
                        ],
                    }
                ],
            )

            response_text = response.choices[0].message.content

            # Extract JSON from the response
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()
            else:
                json_str = response_text.strip()

            result = json.loads(json_str)
            logger.info(f"GPT analysis: {result}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GPT response as JSON: {e}")
            logger.error(f"Raw response: {response_text}")
            return {
                "is_suspicious": False,
                "confidence": "low",
                "description": "Failed to analyze image",
                "reason": f"JSON parse error: {e}",
            }
        except openai.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise
    def analyze_image_for_roommate(self, image_data: bytes) -> dict:
        """
        Given image determine if roommate or friend is contained within image

        :param self: Class var
        :param image_data: Jpeg Image in Bytes 
        :return: determination of roommate + name
        :rtype: dict
        """
        pass
        
         

